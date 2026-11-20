import type { HeatMetric, Heatmap } from '../types'
import { METRIC_THRESHOLDS, continuousScore, discreteRiskRgba, scoreToRgba } from './risk'

const UPSCALE = 6 // pre-smooth on a canvas; the browser then scales the PNG with bilinear filtering

/**
 * Render every forecast hour of one metric to a PNG data URL, once.
 * Moving the slider afterwards is just swapping an <img> src: no refetch, no re-render
 * of the grid, so it stays smooth even on slow machines.
 */
export function buildFrames(heat: Heatmap, metric: HeatMetric): string[] {
  const { nx, ny } = heat.grid
  const small = document.createElement('canvas')
  small.width = nx
  small.height = ny
  const sctx = small.getContext('2d')!
  const big = document.createElement('canvas')
  big.width = nx * UPSCALE
  big.height = ny * UPSCALE
  const bctx = big.getContext('2d')!
  bctx.imageSmoothingEnabled = true
  bctx.imageSmoothingQuality = 'high'

  const frames: string[] = []
  for (let h = 0; h < heat.hours.length; h++) {
    const img = sctx.createImageData(nx, ny)
    for (const c of heat.cells) {
      const v = c[metric][h]
      if (v === null || v === undefined) continue
      const rgba = metric === 'risk' ? discreteRiskRgba(v) : scoreToRgba(continuousScore(v, METRIC_THRESHOLDS[metric]))
      const o = (c.j * nx + c.i) * 4
      img.data[o] = rgba[0]
      img.data[o + 1] = rgba[1]
      img.data[o + 2] = rgba[2]
      img.data[o + 3] = rgba[3]
    }
    sctx.putImageData(img, 0, 0)
    bctx.clearRect(0, 0, big.width, big.height)
    bctx.drawImage(small, 0, 0, big.width, big.height)
    frames.push(big.toDataURL('image/png'))
  }
  return frames
}

/** Max of a metric across the corridor for each hour (drives the mini timeline under the slider). */
export function hourlyPeak(heat: Heatmap, metric: HeatMetric): number[] {
  return heat.hours.map((_, h) => {
    let peak = 0
    for (const c of heat.cells) {
      const v = c[metric][h]
      const score = v == null ? 0 : metric === 'risk' ? v : continuousScore(v, METRIC_THRESHOLDS[metric])
      if (score > peak) peak = score
    }
    return peak
  })
}
