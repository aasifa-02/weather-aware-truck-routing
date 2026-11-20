const tz = Intl.DateTimeFormat().resolvedOptions().timeZone

const dateTime = new Intl.DateTimeFormat(undefined, {
  weekday: 'short',
  month: 'short',
  day: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
  timeZoneName: 'short',
})
const timeOnly = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' })

export const localTimeZone = tz
export const fmtDateTime = (iso: string) => dateTime.format(new Date(iso))
export const fmtTime = (iso: string) => timeOnly.format(new Date(iso))

export function fmtDuration(seconds: number): string {
  const h = Math.floor(seconds / 3600)
  const m = Math.round((seconds % 3600) / 60)
  return h ? `${h} h ${m.toString().padStart(2, '0')} min` : `${m} min`
}

export const fmtMiles = (mi: number) => `${Math.round(mi).toLocaleString()} mi`

/** Value for <input type="datetime-local"> = next full hour in the browser's zone. */
export function defaultDepartureLocal(): string {
  const d = new Date()
  d.setMinutes(0, 0, 0)
  d.setHours(d.getHours() + 1)
  return toLocalInput(d)
}

export function toLocalInput(d: Date): string {
  const pad = (n: number) => n.toString().padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** datetime-local string -> ISO with the browser's UTC offset. */
export function localInputToIso(value: string): string {
  return new Date(value).toISOString()
}
