function validate(slot) {
  if (!Number.isInteger(slot) || slot < 0 || slot > 287) throw new RangeError('Invalid five-minute slot');
}

export function mockProfile(slot) {
  validate(slot);
  const hour = slot / 12;
  const demand = Math.round(635 + 110 * Math.sin((hour - 6) * Math.PI / 24) ** 2);
  const wind = Math.round(110 + 55 * Math.sin(hour * Math.PI / 12 + 1));
  const solar = Math.round(280 * Math.max(0, Math.sin((hour - 6) * Math.PI / 12)));
  return { data_kind: 'mock', demand, wind, solar, netLoad: demand - wind - solar };
}

export function timeLabel(slot) {
  validate(slot);
  return `${String(Math.floor(slot / 12)).padStart(2, '0')}:${String(slot % 12 * 5).padStart(2, '0')}`;
}
