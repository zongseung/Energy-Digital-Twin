# Two rotating turbines — verification, 2026-09-30 KST

Result: the deployed program matches its documented illustrative model; the two/eight split is caused by the nearest-station inputs and the model's below-3 m/s zero-RPM convention. It is not evidence that eight real offshore turbines have stopped.

## Captured live inputs

Browser snapshot observed 2026-09-30 03:47 KST, received 03:52:35 KST:

| Turbine IDs | Station | Ground wind | Visual RPM per turbine |
|---|---|---:|---:|
| 5722–5723 (2) | 고산185 | 3.7 m/s | 4.468461538 |
| 5724–5731 (8) | 낙천990 | 1.5 m/s | 0 |

The live stream subsequently changed Nakcheon to 1.3 m/s at 03:48 KST; this remained below the model threshold. Times above refer to observations, not capture time.

## Verification

- Actual Chromium through OMO omowright, against the deployed app and its real weather WebSocket: only rotor IDs5722/5723 changed angle after enabling the existing rotation checkbox.
- Counterfactual performed only in the owned test page: change Nakcheon input to4 m/s → all10 rotor angles advance; restore the captured input → only2 advance again. No production files, source observations, API cache, or running service were altered. Existing roadview work was preserved.
- Existing `node tests/wind-estimate.mjs` passed. Independent 50,001-point numerical sweep from0 to50 m/s confirmed bounds, finite results, piecewise arithmetic and invalid-input rejection. RPM-to-angle conversion is correctly `seconds × RPM × 2π/60`.
- Example arithmetic: at3.6 m/s,27.174193548 kW /4.347692308 RPM; at1.7 m/s,0/0. At exactly3 m/s the animation jumps to3.623076923 RPM while the illustrative electrical curve is0 kW. This is a visualization assumption, not a measured controller response.
- Deployed estimator matches local SHA256 `8e30fe9ce7332361db2b6122dd96360ec9525f8cf29ebdfa681259e904431026`.

## Physical limitation

[Doosan-authored WinDS3000 sheet](https://www.visiongroup21.eu/en/pdf/tech6c.pdf) supplies 3/13/25 m/s cut-in/rated/cut-out and15.7 rated RPM, but does not provide the current linear RPM control curve or a below-cut-in zero-RPM rule. [DOE wind guidebook](https://www.energy.gov/cmei/systems/windexchange/small-wind-guidebook) distinguishes electrical cut-in from rotor start-up speed and warns that weather-station wind is not necessarily the wind at the site/hub.

The arithmetic is internally consistent. Ground-station nearest-neighbor assignment plus electrical cut-in used as an animation stop condition is a coarse assumption. No justified universal hub-height multiplier or actual turbine start-up/control curve is available. This verification does not change the formula or force all rotors to move.
