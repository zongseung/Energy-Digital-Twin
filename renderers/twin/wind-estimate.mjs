// Illustrative WinDS3000/91 scenario, not its measured or manufacturer power curve.
// Assumed 3/13/25 m/s thresholds and 15.7 rated RPM: https://www.visiongroup21.eu/en/pdf/tech6c.pdf
// Cubic power: https://www.energy.gov/cmei/systems/windexchange/small-wind-guidebook
// Constant tip-speed-ratio scenario: https://www.nrel.gov/docs/fy04osti/35816.pdf
const CUT_IN_M_S = 3, RATED_M_S = 13, CUT_OUT_M_S = 25;
const RATED_POWER_KW = 3000, RATED_RPM = 15.7;
export function estimateWind(speed) {
  if (!Number.isFinite(speed) || speed < 0) return null;
  if (speed < CUT_IN_M_S) return {powerKW:0, rpm:0, condition:'below'};
  if (speed >= CUT_OUT_M_S) return {powerKW:0, rpm:0, condition:'above'};
  return {
    powerKW:speed < RATED_M_S ? RATED_POWER_KW * (speed ** 3 - CUT_IN_M_S ** 3) / (RATED_M_S ** 3 - CUT_IN_M_S ** 3) : RATED_POWER_KW,
    rpm:RATED_RPM * Math.min(speed / RATED_M_S, 1),
    condition:'available',
  };
}
