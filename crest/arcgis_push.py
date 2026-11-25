"""Load CREST's feeds into hosted feature layers in VIMS's ArcGIS Online org, for the ArcGIS
Experience Builder dashboard (Esri UC). Each build overwrites the layers in place, so the dashboard
and its maps keep pointing at the same items.

    python -m crest.arcgis_push site/feeds

Credentials, from repository secrets (the step is skipped without them):
    ARCGIS_URL        the org's URL, e.g. https://vims.maps.arcgis.com
    ARCGIS_API_KEY    an API key credential with privileges to create and update items, or
    ARCGIS_USERNAME / ARCGIS_PASSWORD   a built-in ArcGIS Online account allowed to publish
Optional: ARCGIS_FOLDER (default "StormSense CREST").

The first run publishes each CSV as a hosted feature layer in that folder (points from the lon/lat
columns, dates detected by ArcGIS); later runs overwrite them. Sharing stays as Derek sets it.
Daily Skill is time-enabled on its "date" field so the map's time slider can replay a period.
"""
import os
import sys

LAYERS = [
    # title, file, has lon/lat
    ("CREST Stations", "stations.csv", True),
    ("CREST Daily Skill", "daily_skill.csv", True),
    ("CREST Flood Events", "flood_events.csv", True),
    ("CREST Leaderboard", "leaderboard.csv", False),
    ("CREST Lead Decay", "lead_decay.csv", False),
    ("CREST Changes", "changes.csv", False),
]
TAGS = "StormSense, CREST, VIMS, storm tide, forecast skill"
SNIPPET = "StormSense CREST: archived storm tide forecasts scored against the gauges (VIMS)."


def connect():
    from arcgis.gis import GIS
    url = os.environ.get("ARCGIS_URL", "").strip()
    key = os.environ.get("ARCGIS_API_KEY", "").strip()
    user, pw = os.environ.get("ARCGIS_USERNAME", "").strip(), os.environ.get("ARCGIS_PASSWORD", "")
    if not url or not (key or (user and pw)):
        return None
    return GIS(url, api_key=key) if key else GIS(url, user, pw)


def find(gis, title, item_type):
    me = gis.users.me.username if gis.users.me else None
    q = f'title:"{title}"' + (f" AND owner:{me}" if me else "")
    return next((i for i in gis.content.search(q, item_type=item_type, max_items=50) if i.title == title), None)


def add_csv(gis, title, path, folder):
    props = {"title": title, "type": "CSV", "tags": TAGS, "snippet": SNIPPET}
    try:  # arcgis 2.3+: folders own their items
        f = gis.content.folders.get(folder) or gis.content.folders.create(folder)
        job = f.add(item_properties=props, file=path)
        return job.result() if hasattr(job, "result") else job
    except AttributeError:
        return gis.content.add(props, data=path, folder=folder)


def publish(gis, src, has_xy, title):
    params = gis.content.analyze(item=src.id, file_type="csv", location_type="coordinates" if has_xy else "none")
    pp = params["publishParameters"]
    pp["name"] = title.replace(" ", "_")
    if has_xy:
        pp.update(locationType="coordinates", latitudeFieldName="lat", longitudeFieldName="lon")
    else:
        pp["locationType"] = "none"
    return src.publish(publish_parameters=pp)


def time_enable(layer_item, field="date"):
    try:
        lyr = layer_item.layers[0]
        if (lyr.properties.get("timeInfo") or {}).get("startTimeField"):
            return
        lyr.manager.update_definition({"timeInfo": {"startTimeField": field, "endTimeField": None, "trackIdField": "station",
                                                    "timeReference": {"timeZone": "UTC"}, "timeInterval": 1,
                                                    "timeIntervalUnits": "esriTimeUnitsDays"}})
        print("  time-enabled on", field)
    except Exception as e:
        print("  could not time-enable:", e)


def main(feeds):
    gis = connect()
    if gis is None:
        print("ArcGIS credentials not set (ARCGIS_URL and ARCGIS_API_KEY or ARCGIS_USERNAME/PASSWORD); nothing pushed")
        return 0
    from arcgis.features import FeatureLayerCollection
    folder = os.environ.get("ARCGIS_FOLDER", "StormSense CREST")
    failed = 0
    for title, name, xy in LAYERS:
        path = os.path.join(feeds, name)
        if not os.path.exists(path):
            print(f"{title}: {name} missing, skipped")
            continue
        try:
            layer = find(gis, title, "Feature Layer")
            if layer is None:
                src = find(gis, title, "CSV") or add_csv(gis, title, path, folder)
                layer = publish(gis, src, xy, title)
                print(f"{title}: published {layer.homepage}")
            else:
                res = FeatureLayerCollection.fromitem(layer).manager.overwrite(path)
                ok = res.get("success", True) if isinstance(res, dict) else bool(res)
                print(f"{title}: overwritten ({'ok' if ok else res})")
                failed += 0 if ok else 1
            if name == "daily_skill.csv":
                time_enable(layer)
        except Exception as e:
            failed += 1
            print(f"::warning title=ArcGIS {title}::{e}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "site/feeds"))
