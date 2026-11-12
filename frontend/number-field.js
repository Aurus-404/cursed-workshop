// A numeric field shows two decimals: the gizmo leaves values like
// -6.18245068359375 that do not fit the field. Only typing a value changes
// the stored one; showing it rounded changes nothing.
export function shown(value){const number=Number(value);return Number.isFinite(number)?String(Math.round(number*100)/100):value;}
