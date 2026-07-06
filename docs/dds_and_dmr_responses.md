# Sample DDS and DMR responses
Includes the response headers, and the command used to get these responses. 

Here's what comes back from Hyrax right now:
```
(discover) ➜  discover git:(main) curl -i http://test.opendap.org/opendap/data/nc/fnoc1.nc.dmr
HTTP/1.1 200 200
Date: Mon, 06 Jul 2026 09:37:54 GMT
Server: Hyrax/1.17.1-1440-test-deploy
X-FRAME-OPTIONS: DENY
Last-Modified: Thu, 23 Sep 2021 19:45:14 GMT
XDODS-Server: dods/3.2
XOPeNDAP-Server: asciival/, bes/, csv_handler/, dapreader_module/, dmrpp_module/, fileout_covjson/, fileout_json/, fileout_netcdf/, freeform_handler/, functions/, gateway/, gdal_module/, hdf4_handler/, hdf5_handler/, libdap/, netcdf_handler/, s3_reader/, usage/, xml_data_handler/
X-DAP: 3.2
Content-Description: application/vnd.opendap.dap4.dataset-metadata+xml
Content-Type: application/vnd.opendap.dap4.dataset-metadata+xml
Transfer-Encoding: chunked

<?xml version="1.0" encoding="ISO-8859-1"?>
<Dataset xmlns="http://xml.opendap.org/ns/DAP/4.0#" xml:base="http://test.opendap.org/opendap/data/nc/fnoc1.nc" dapVersion="4.0" dmrVersion="1.0" name="fnoc1.nc">
```

From the TDS:
```
(discover) ➜  discover git:(jhrg/6-fix-on-dmr) curl -i https://pae-paha.pacioos.hawaii.edu/thredds/dodsC/dhw_5km.dds
HTTP/1.1 200 
Date: Mon, 06 Jul 2026 09:45:05 GMT
Server: Apache/2.4.29 (Ubuntu)
XDODS-Server: opendap/3.7
Content-Description: dods-dds
Content-Type: text/plain
Vary: Accept-Encoding
Access-Control-Allow-Origin: *
Transfer-Encoding: chunked

Dataset {
    Float32 latitude[latitude = 3600];
```

Getting DAP4 responses in other pages is done usiing 'thredds/dap4':

```
(base) ➜  tds-5.8 curl -i https://thredds.ucar.edu/thredds/dap4/satellite/goes/18/grb/EXIS/SFXR/20260622/OR_EXIS-L1b-SFXR_G18_s20261730000294_e20261730000589_c20261730000592.nc.dmr.xml
HTTP/1.1 200 200
Date: Mon, 06 Jul 2026 10:54:04 GMT
Server: Apache
X-Frame-Options: SAMEORIGIN
X-Content-Type-Options: nosniff
Strict-Transport-Security: max-age=63072000; includeSubdomains;
Content-Description: application/vnd.opendap.dap4.dataset-metadata+xml
Content-Type: text/xml;charset=utf-8
Vary: Accept-Encoding
X-XSS-Protection: 1; mode=block
Content-Security-Policy: default-src 'none'; base-uri 'self' https://www.opendap.org; form-action 'self'; frame-ancestors 'self'; frame-src 'self'; object-src 'none'; img-src 'self' data: https://a.tile.openstreetmap.org https://b.tile.openstreetmap.org https://c.tile.openstreetmap.org https://www.unidata.ucar.edu https://ahocevar.com; style-src 'self' 'unsafe-inline' https://necolas.github.io https://cdnjs.cloudflare.com; font-src 'self'; connect-src 'self'; media-src 'self'; manifest-src 'self'; worker-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdnjs.cloudflare.com https://cdn.rawgit.com https://cdn.jsdelivr.net
Transfer-Encoding: chunked

<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Dataset
         name="OR_EXIS-L1b-SFXR_G18_s20261730000294_e20261730000589_c20261730000592.nc"
         dapVersion="4.0"
         dmrVersion="1.0"
         xmlns="http://xml.opendap.org/ns/DAP/4.0#"
         xmlns:dap="http://xml.opendap.org/ns/DAP/4.0#">
    <Dimension name="number_of_time_bounds" size="2"/>
    <Dimension name="sps_measurement_count" size="4"/>
```

From ERDDAP:
```
(base) ➜  tds-5.8 curl -i https://coastwatch.pfeg.noaa.gov/erddap/tabledap/pmelTaoDySst.dds
HTTP/1.1 200 
Date: Mon, 06 Jul 2026 10:23:28 GMT
Server: Apache
Strict-Transport-Security: max-age=31536000; includeSubDomains; preload
X-Frame-Options: SAMEORIGIN, SAMEORIGIN
Last-Modified: Mon, 06 Jul 2026 10:23:28 GMT
xdods-server: dods/3.7
erddap-server: 2.30.0
Content-Description: dods-dds
Content-Encoding: identity
Content-Type: text/plain;charset=ISO-8859-1
Vary: Accept-Encoding
X-XSS-Protection: 1; mode=block
X-Content-Type-Options: nosniff
Cross-Origin-Opener-Policy: same-origin
Content-Security-Policy: script-src 'self' https://accounts.google.com https://apis.google.com https://code.jquery.com/ https://www.google-analytics.com https://www.googletagmanager.com https://www.gstatic.com https://stackpath.bootstrapcdn.com https://fp1.formmail.com https://coastwatch.noaa.gov https://polarwatch.noaa.gov 'unsafe-inline' 'unsafe-eval'; font-src 'self' https://fonts.googleapis.com https://stackpath.bootstrapcdn.com; frame-ancestors 'self'  https://heatherwelch.shinyapps.io;
Connection: close
Transfer-Encoding: chunked

Dataset {
  Sequence {
    String array;
    String station;
    Int32 wmo_platform_code;
    Float32 longitude;
    Float32 latitude;
    Float64 time;
    Float32 depth;
    Float32 T_25;
    Float32 QT_5025;
    Float32 ST_6025;
  } s;
} s;
(base) ➜  tds-5.8 
```