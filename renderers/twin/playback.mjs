// Pure guards for the state panel's time modes. No DOM.
export function shouldApply(mode, requestedAt, responseAt, isLive, requestedSeq, responseSeq) {
  if (mode === 'latest') return isLive;
  if (mode === 'history') return !isLive && requestedSeq === responseSeq && Date.parse(requestedAt) === Date.parse(responseAt);
  return false; // scenario: observations never overwrite a simulation view
}
export function kstDay(date) {
  const day = /^\d{4}-\d{2}-\d{2}$/.test(date) ? new Date(`${date}T00:00:00Z`) : null;
  if (!day || Number.isNaN(day.getTime()) || day.toISOString().slice(0, 10) !== date) return null;
  day.setUTCDate(day.getUTCDate() + 1);
  return {start:`${date}T00:00:00+09:00`, end:`${day.toISOString().slice(0, 10)}T00:00:00+09:00`};
}
