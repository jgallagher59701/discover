(discover) ➜  discover git:(main) ✗ grep erddap dap_endpoints.jsonl | wc -l
     820
(discover) ➜  discover git:(main) ✗ grep -v erddap dap_endpoints.jsonl | wc -l
       1
(discover) ➜  discover git:(main) ✗ wc -l candidate_urls.2026.21.txt
   11913 candidate_urls.2026.21.txt
(discover) ➜  discover git:(main) ✗ grep erddap candidate_urls.2026.21.txt | wc -l
    8655
(discover) ➜  discover git:(main) ✗ grep -v erddap candidate_urls.2026.21.txt | wc -l
    3258

Things in candidate_urls_not_erddap.txt (i.e., grep -v erddap candidate_urls.2026.21.txt)
that did not get crawled:

http://sgbd.acmad.org:8080/thredds/catalog/ACMAD/WWFD/forecastinservice/ensemble5/2022/202201/20220104/catalog.html?dataset=ACMAD/WWFD/forecastinservice/ensemble5/2022/202201/20220104/ARP20220104_48.png

http://tds.hycom.org/thredds/catalogs/ESPC-D-V02_u3z.html?dataset=ESPC-D-V02-u3z

https://gcoos5.geos.tamu.edu/thredds/catalog/catalog.html

https://geoport.usgs.esipfed.org/thredds/catalog/silt/usgs/Projects/stellwagen/CF-1.6/BW2011/catalog.html

https://ncss.hycom.org/thredds/catalog.html

https://pae-paha.pacioos.hawaii.edu/thredds/catalog/ww3_mariana/catalog.html?dataset=ww3_mariana/WaveWatch_III_Mariana_Regional_Wave_Model_best.ncd
https://pae-paha.pacioos.hawaii.edu/thredds/dodsC/hmrg_bathytopo_50m_mhi.html
https://pae-paha.pacioos.hawaii.edu/thredds/dodsC/neowave_nawiliwili.html

https://tds-nexrad.scigw.unidata.ucar.edu/thredds/catalog/catalog.html
https://tds-opal.sr.unh.edu/thredds/catalog/opal_ts/altimeter/wav_files/jason1/catalog.html
https://tds-opal.sr.unh.edu/thredds/catalog/opal_ts/altimeter/wav_files/jason2/catalog.html
https://tds.gdex.ucar.edu/thredds/catalog/catalog.html
https://tds.hycom.org/thredds/catalog.html
https://tds.hycom.org/thredds/catalogs/ESPC-D-V02_t3z.html?dataset=ESPC-D-V02-t3z-2026
