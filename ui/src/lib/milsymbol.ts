import _ms from 'milsymbol';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const MilSymbol: new (sidc: string, opts: object) => { asSVG(): string } =
  (_ms as any).Symbol ?? _ms;

const ICON_CSS = 44;
const CACHE = new Map<string, ImageData>();

/** Render a milsymbol SIDC to an ImageData suitable for map.addImage(). */
export async function loadSidcImage(sidc: string): Promise<ImageData> {
  const hit = CACHE.get(sidc);
  if (hit) return hit;

  const dpr = Math.ceil(window.devicePixelRatio || 1);
  const px = ICON_CSS * dpr;

  let svgStr: string;
  try {
    svgStr = new MilSymbol(sidc, { size: 32 }).asSVG();
  } catch {
    svgStr = `<svg xmlns="http://www.w3.org/2000/svg" width="${ICON_CSS}" height="${ICON_CSS}">
      <circle cx="${ICON_CSS / 2}" cy="${ICON_CSS / 2}" r="${ICON_CSS / 2 - 3}"
        fill="#888" stroke="#fff" stroke-width="2"/>
    </svg>`;
  }

  return new Promise<ImageData>((resolve) => {
    const img = new Image();
    const url = URL.createObjectURL(
      new Blob([svgStr], { type: 'image/svg+xml;charset=utf-8' })
    );
    const draw = (ok: boolean) => {
      const c = document.createElement('canvas');
      c.width = px; c.height = px;
      const ctx = c.getContext('2d')!;
      if (ok && img.naturalWidth > 0) {
        const s = Math.min(px / img.naturalWidth, px / img.naturalHeight) * 0.85;
        const w = img.naturalWidth * s, h = img.naturalHeight * s;
        ctx.drawImage(img, (px - w) / 2, (px - h) / 2, w, h);
      } else {
        ctx.fillStyle = '#888';
        ctx.beginPath();
        ctx.arc(px / 2, px / 2, px / 2 - 4, 0, Math.PI * 2);
        ctx.fill();
      }
      URL.revokeObjectURL(url);
      const data = ctx.getImageData(0, 0, px, px);
      CACHE.set(sidc, data);
      resolve(data);
    };
    img.onload = () => draw(true);
    img.onerror = () => draw(false);
    img.src = url;
  });
}

/** SIDC template per unit_type. Position 1 (affiliation) is '_' — substitute F or H. */
const SIDC_TEMPLATE: Record<string, string> = {
  f16c:              'S_APMFF----E---',
  f35a:              'S_APMFF----E---',
  eurofighter:       'S_APMFF----E---',
  su27s:             'S_APMFF----E---',
  su30sm:            'S_APMFF----E---',
  su34:              'S_APMFF----E---',
  a10c:              'S_APMCF----E---',
  su25:              'S_APMCF----E---',
  challenger2:       'S_GPUC-----E---',
  t80u:              'S_GPUC-----E---',
  leopard2a6:        'S_GPUC-----E---',
  infantry_mech:     'S_GPUCI----E---',
  infantry_light:    'S_GPUCI----E---',
  vdv_btg:           'S_GPUCIA---E---',
  himars:            'S_GPUCRH---E---',
  mlrs_m270:         'S_GPUCRH---E---',
  iskander_m:        'S_GPUCRH---E---',
  patriot_pac3:      'S_GPAMAD---E---',
  s300v4:            'S_GPAMAD---E---',
  buk_m3:            'S_GPAMAD---E---',
  pantsir_s1:        'S_GPAMAD---E---',
  nasams:            'S_GPAMAD---E---',
  frigate_hnlms:     'S_SPCLFF---E---',
  destroyer_arleigh: 'S_SPCLDD---E---',
  slava_cg:          'S_SPCLCC---E---',
  submarine_ula:     'S_SPCLS----E---',
};

export function sidcForUnit(unitType: string, side: 'blue' | 'red'): string {
  const t = SIDC_TEMPLATE[unitType] ?? 'S_GPUCI----E---';
  return t.replace('_', side === 'blue' ? 'F' : 'H');
}
