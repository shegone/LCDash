# Private GIS reference layers

LCDash can show reviewed reference layers beneath live CAD calls and fresh unit
locations. This is an operator-only map feature; it does not write to CAD,
change CAD geocoding, or replace authoritative GIS records.

## First package

The first deployment accepts the supplied NAD83 geographic source archives:

- `Roads.zip`
- `Boundary-polygons.zip`
- `ESB-polygons.zip`

It publishes only these reviewed GeoJSON filenames under
`/srv/lcdash-data/gis-public`:

| Layer | Output file | Browser properties |
| --- | --- | --- |
| County | `county_boundary.geojson` | County name |
| PSAP | `psap_boundary.geojson` | PSAP name |
| Municipalities | `municipalities.geojson` | Municipality name |
| Provisioning | `provisioning_boundary.geojson` | Boundary name/type |
| Fire ESB | `esb_fire.geojson` | Agency name |
| EMS ESB | `esb_ems.geojson` | Agency name |
| Law ESB | `esb_law.geojson` | Agency name |
| Roads | `roads.geojson` | Local street name, road class |

Raw archives and shapefile sidecars belong only in
`/srv/lcdash-data/gis-source`; they are not committed, mounted into the web
application, or served to a browser. The app receives only the generated
output directory as a read-only mount and removes all fields outside the table
above again before sending a layer to the map.

## Deliberately deferred

Addresses remain server-side for a later authenticated lookup feature. The
trail and mile-marker archives use a different projected coordinate system and
are not part of the first package.

## Import requirement

Use a one-shot GDAL import job, with field allowlists and `EPSG:4326` output,
before copying a source archive or generated output to production. Do not run
an importer inside the LCDash web container.

On the private production server, stage the three reviewed archives under
`/srv/lcdash-data/gis-source` and generate into
`/srv/lcdash-data/gis-public` with a temporary GDAL container. Run each command
only after reviewing the source archive and output directory:

```sh
docker run --rm -v /srv/lcdash-data/gis-source:/source:ro -v /srv/lcdash-data/gis-public:/output ghcr.io/osgeo/gdal:alpine-small-latest \
  ogr2ogr -f GeoJSON -t_srs EPSG:4326 -select 'LSt_Name,RoadClass' -lco RFC7946=YES /output/roads.geojson /vsizip//source/Roads.zip/Roads.shp
```

Use the equivalent reviewed source/output mapping for the other layers:

| Source layer | Output | Allowed source fields |
| --- | --- | --- |
| `CountyBoundary.shp` | `county_boundary.geojson` | `County` |
| `PSAPBoundary.shp` | `psap_boundary.geojson` | `PSAPName` |
| `IncorporatedMunicipalities.shp` | `municipalities.geojson` | `IncMuni` |
| `ProvisioningBoundary.shp` | `provisioning_boundary.geojson` | `PrvBndNm,PrvBndTp` |
| `ESB_FIRE.shp` | `esb_fire.geojson` | `AgencyName` |
| `ESB_EMS.shp` | `esb_ems.geojson` | `AgencyName` |
| `ESB_LAW.shp` | `esb_law.geojson` | `AgencyName` |
