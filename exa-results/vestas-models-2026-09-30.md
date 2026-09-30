# Vestas / Jeju wind-turbine model research (2026-09-30)

Exa scope: 7 searches × 6 results = `sources_reviewed: 42`; `original_pages_fetched: 11` (including operator, manufacturer-brochure copies, model listings and contemporaneous supply-contract coverage). Searches covered operator/as-built records, manufacturer dimensions, 3D/CAD assets, Sinchang and a targeted Vestas/Hankyung announcement search. Search results were deduplicated qualitatively. The V90 brochure PDF was downloaded and visually inspected; no CAD/3D asset was downloaded.

## As-built records (keep separate from generic models)

- [KOSPO 발전설비현황](https://www.kospo.co.kr/kospo/194/subview.do) (operator primary, fetched): Hankyung 1.5 MW × 4 and 3 MW × 5, commissioned 2007-11, manufacturer VESTAS. It gives no turbine model code, tower/hub dimensions, engineering drawings or CAD files. Does not prove V47/V80/V90.
- [국내 풍력발전설비 운영현황, 2015-12-31](https://www.iwest.co.kr/beffatInfoPublict/iwest/2026/download.do) (public sector table, fetched): Hankyung 1,500 kW ×4 (2004.02) and 3,000 kW ×5 (2007.12), maker Vestas; Sinchang 850 kW ×2 (2006.03), maker Vestas. This supports capacity/count/manufacturer, not model subtype or dimensions. The URL is on iwest.co.kr despite the source title; treat as secondary public operating table until provenance is corroborated.
- [Sinchang – The Wind Power](https://www.thewindpower.net/windfarm_en_10165_sinchang.php) (fetched, tertiary database, last updated 2015): 1.7 MW operating, Vestas ×2; explicitly no source available. It agrees on count/capacity but is weaker than the operating table.

## Manufacturer specifications (reference only)

- [V90-3.0 MW brochure/specification PDF](https://www.maine.gov/dacf/lupc/projects/windpower/transcanada/Volume3/Volume3_Section2/Appendix%202-I.pdf) (6 pages; Vestas-authored brochure reproduced in a project filing; downloaded, SHA-256 `f2269e5c0cc77f01150d5a257513086653bec4aae15ffb7c3ad9e04d2ab191ef`; local copy `var/research/drawings/vestas-v90-3mw-spec.pdf`). Page 3 has a rendered nacelle cutaway/component-location illustration keyed to 17 parts (coolers, transformer, sensors, controller, crane, generator, coupling, yaw gears, gearbox, brake, machine foundation, blade bearing, hub, blade, pitch cylinder, hub controller), plus a power curve. Page 4 gives principal turbine data: 90 m rotor diameter, 6,362 m² swept area, 80/105 m hub heights, 4/15/25 m/s cut-in/rated/cut-out speeds. These are component-layout illustration and headline specifications, not dimensioned manufacturing or foundation drawings. This is a generic V90-3.0 reference; the Hankyung match is not proved by the operator/manufacturer evidence.
- [V80-1.8 MW brochure PDF](https://www.pse.com/-/media/PDFs/VEStas_V80_18_US77377.pdf) (manufacturer brochure hosted by utility, fetched): 80 m rotor, hub heights approx. 60/67/78 m. Does not match an evidenced Hankyung nameplate and cannot be assumed as that site's 1.5 MW model.
- [V47 technical specification PDF](https://www.ledsjovind.se/ventosum/Vestas_V47.pdf) (Vestas brochure copy, fetched): 47 m rotor; brochure covers 660 kW / 660-200 kW, not Hankyung's 1.5 MW or Sinchang's 850 kW units. Not a plausible evidence-backed subtype match.
- [V90-1.8/2.0 MW brochure PDF](https://havsnas.se/vindkraft/090821_Product-brochure-V90-1.8-2.0MW-06-09-EN.pdf) (Vestas brochure copy, fetched): V90 rotor 90 m, 1.8/2.0 MW variants. It is not the 3 MW V90; no Hankyung tie established.

## Actual model files

- [CGTrader: Vestas V90-2-105 model](https://www.cgtrader.com/3d-models/exterior/industrial-exterior/wind-turbine-vestas-v90-2-105) (creator-provided paid asset listing, fetched): lists 3DS, DAE, 3ds Max, AutoCAD, DXF, Lightwave, other and textures. Prices/payment gating apply; actual files were not acquired or independently opened. Creator describes it as near-scale; listing's V90 2 MW is not Hankyung's documented 3 MW capacity. Generic visualization only.
- [Wind-Turbine-Models V80 own-build page](https://en.wind-turbine-models.com/models/54-vestas-v80-2-0) (fetched): model/build record but says “There are no files stored”; no CAD download.
- [Wind-Turbine-Models V90 own-build page](https://en.wind-turbine-models.com/models/594-vestas-v90) (fetched): physical 1:50 model described as V90 3 MW, but says no files stored; not a digital model/download.
- [RenderHub Vestas wind turbine](https://www.renderhub.com/3dxin/wind-turbine-vestas) (search result, not fetched): paid $22.75 listing, 55.9 MB, formats claimed include 3DS/OBJ/FBX/DWG. It is a generic Vestas image/render asset, no exact type or dimension evidence; not downloaded.

## Finding

The targeted contemporaneous press search found a 2006 report that STX had signed a 15 MW Hankyung stage-two supply contract with KOSPO; it confirms contract scope, not Vestas model code or completion. The Wind Power database currently lists five Hankyung V90/3000 units and four NM72c/1500 units, but explicitly gives “No source available”; treat that as a lead, not verified installed-model evidence. The 2008 commissioning report independently confirms five installed 3 MW Vestas machines, without model code. Thus V90/3000 is plausible and reported by a tertiary database, but remains unconfirmed by primary evidence.

No publicly downloadable as-built CAD/BIM/3D file or dimensioned construction drawing for Hankyung or Sinchang was found. Public brochure material does include a nacelle component-layout illustration and principal dimensions; distinguish these from fabrication/installation drawings. A paid creator-uploaded generic V90 model listing exists, but it is not site-specific. Do not treat the V90 clue as established without an operator nameplate/technical source.
