export function menuLightshowRandom(seed: number, channel: number, step: number) {
  let value = seed ^ Math.imul(channel + 1, -1_640_531_527) ^ Math.imul(step + 1, -2_048_140_359);
  value = Math.imul(value ^ (value >>> 16), 2_147_612_853);
  value = Math.imul(value ^ (value >>> 15), 1_598_334_677);
  return ((value ^ (value >>> 16)) >>> 0) / 4_294_967_296;
}
