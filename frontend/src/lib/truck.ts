import type { RouteOption } from '../types'

/** Where the truck is at `iso` on a route, interpolated between checkpoint ETAs. */
export function truckPosition(route: RouteOption, iso: string): { pos: [number, number]; mile: number; status: 'before' | 'enroute' | 'arrived' } {
  const t = Date.parse(iso)
  const cps = route.checkpoints
  const first = cps[0]
  const last = cps[cps.length - 1]
  if (t <= Date.parse(first.eta)) return { pos: [first.lat, first.lng], mile: 0, status: 'before' }
  if (t >= Date.parse(last.eta)) return { pos: [last.lat, last.lng], mile: last.mile, status: 'arrived' }
  for (let k = 1; k < cps.length; k++) {
    const b = cps[k]
    const tb = Date.parse(b.eta)
    if (t <= tb) {
      const a = cps[k - 1]
      const ta = Date.parse(a.eta)
      const f = tb === ta ? 0 : (t - ta) / (tb - ta)
      return {
        pos: [a.lat + (b.lat - a.lat) * f, a.lng + (b.lng - a.lng) * f],
        mile: a.mile + (b.mile - a.mile) * f,
        status: 'enroute',
      }
    }
  }
  return { pos: [last.lat, last.lng], mile: last.mile, status: 'arrived' }
}
