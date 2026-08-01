/* Per-system controller layouts for the phone remote.
 *
 * Each layout maps a real console's controls onto the server's RetroPad button
 * vocabulary (up/down/left/right/a/b/x/y/l1/l2/r1/r2/l3/r3/start/select/menu +
 * analog axes). The remote reads the active session's `platform` and renders the
 * matching pad, so a Game Boy shows two buttons and a PlayStation shows two
 * sticks and four shapes — authentic per console, not one generic pad.
 *
 * A "face" entry: {k, label, color, ring} placed on a 3x3 face grid via `pos`
 * (row,col 0-2). `dpad`, `start`, `select`, shoulders, and `sticks` are declared
 * per system. Colors follow each console's real button colors.
 */
(function (global) {
  // Shared palettes
  const XBOX = { a: '#4ADE80', b: '#F87171', x: '#60A5FA', y: '#FCD34D' };
  const SNES = { a: '#E45D6A', b: '#FBBf47', x: '#5B7CE6', y: '#3F9E4D' }; // JP SNES colors
  const PS = { a: '#6FB3E0', b: '#E27A9E', x: '#7FC9A6', y: '#D9A441' };  // ○ ✕ △ □ tints
  const NEUTRAL = '#7DD3FC';

  // Reusable face-button builders
  const twoBtn = (aLabel, bLabel, pal) => [
    { k: 'b', label: bLabel, color: pal.b, pos: [1, 0] },
    { k: 'a', label: aLabel, color: pal.a, pos: [1, 2] },
  ];
  const fourDiamond = (pal, labels) => [
    { k: 'y', label: labels.y, color: pal.y, pos: [1, 0] },
    { k: 'x', label: labels.x, color: pal.x, pos: [0, 1] },
    { k: 'b', label: labels.b, color: pal.b, pos: [2, 1] },
    { k: 'a', label: labels.a, color: pal.a, pos: [1, 2] },
  ];

  // Layout catalog. `L` is the default fallback (SNES-like).
  const LAYOUTS = {
    // --- 2-button handhelds / 8-bit ---
    gb:  { name: 'Game Boy', dpad: true, faces: twoBtn('A', 'B', XBOX),
           start: true, select: true, tint: '#9BBC0F' },
    gbc: { extends: 'gb', name: 'Game Boy Color' },
    nes: { name: 'NES', dpad: true, faces: twoBtn('A', 'B', { a: '#C4302B', b: '#C4302B' }),
           start: true, select: true, tint: '#7C3AED' },
    famicom: { extends: 'nes', name: 'Famicom' },
    fds: { extends: 'nes', name: 'Famicom Disk System' },
    sms: { name: 'Master System', dpad: true, faces: twoBtn('1', '2', { a: '#E11D48', b: '#E11D48' }),
           start: false, select: false, pause: true, tint: '#1D4ED8' },
    'sega-master-system': { extends: 'sms' },
    atari2600: { name: 'Atari 2600', dpad: true, faces: [{ k: 'a', label: 'FIRE', color: '#E11D48', pos: [1, 1] }],
                 start: false, select: false, reset: true, tint: '#8B4513' },
    'atari-2600': { extends: 'atari2600' },

    // --- 4-button 16-bit ---
    snes: { name: 'Super Nintendo', dpad: true,
            faces: fourDiamond(SNES, { a: 'A', b: 'B', x: 'X', y: 'Y' }),
            l1: 'L', r1: 'R', start: true, select: true, tint: '#8B7EC8' },
    sfam: { extends: 'snes', name: 'Super Famicom' },
    satellaview: { extends: 'snes' },
    gba: { name: 'Game Boy Advance', dpad: true,
           faces: twoBtn('A', 'B', SNES), l1: 'L', r1: 'R',
           start: true, select: true, tint: '#4B0082' },
    genesis: { name: 'Genesis', dpad: true,
               faces: [{ k: 'y', label: 'A', color: '#DC2626', pos: [1, 0] },
                       { k: 'b', label: 'B', color: '#DC2626', pos: [1, 1] },
                       { k: 'a', label: 'C', color: '#DC2626', pos: [1, 2] },
                       { k: 'x', label: 'X', color: '#B91C1C', pos: [0, 0] },
                       { k: 'l1', label: 'Y', color: '#B91C1C', pos: [0, 1] },
                       { k: 'r1', label: 'Z', color: '#B91C1C', pos: [0, 2] }],
               start: true, select: false, sixbtn: true, tint: '#1E3A8A' },
    'genesis-slash-megadrive': { extends: 'genesis' },
    'sega-mega-drive': { extends: 'genesis' },
    'sega-cd': { extends: 'genesis', name: 'Sega CD' },
    segacd: { extends: 'genesis', name: 'Sega CD' },
    sega32: { extends: 'genesis', name: 'Sega 32X' },
    sega32x: { extends: 'genesis', name: 'Sega 32X' },
    gamegear: { name: 'Game Gear', dpad: true, faces: twoBtn('1', '2', { a: '#DC2626', b: '#DC2626' }),
                start: true, select: false, tint: '#0F172A' },
    'game-gear': { extends: 'gamegear' },

    // --- analog-stick 3D consoles ---
    n64: { name: 'Nintendo 64', dpad: true,
           faces: [{ k: 'a', label: 'A', color: '#2563EB', pos: [2, 1] },
                   { k: 'b', label: 'B', color: '#16A34A', pos: [1, 0] },
                   { k: 'r2', label: 'C', color: '#FBBF24', pos: [1, 2] }],
           l1: 'L', r1: 'R', l2: 'Z', start: true, select: false,
           sticks: ['left'], tint: '#374151' },
    psx: { name: 'PlayStation', dpad: true,
           faces: fourDiamond(PS, { a: '○', b: '✕', x: '△', y: '□' }),
           l1: 'L1', r1: 'R1', l2: 'L2', r2: 'R2',
           start: true, select: true, sticks: ['left', 'right'], tint: '#334155' },
    ps: { extends: 'psx' },
    ps2: { extends: 'psx', name: 'PlayStation 2' },
    psp: { name: 'PSP', dpad: true, faces: fourDiamond(PS, { a: '○', b: '✕', x: '△', y: '□' }),
           l1: 'L', r1: 'R', start: true, select: true, sticks: ['left'], tint: '#1E293B' },
    saturn: { name: 'Saturn', dpad: true,
              faces: [{ k: 'y', label: 'X', color: '#6366F1', pos: [0, 0] },
                      { k: 'x', label: 'Y', color: '#6366F1', pos: [0, 1] },
                      { k: 'r1', label: 'Z', color: '#6366F1', pos: [0, 2] },
                      { k: 'b', label: 'A', color: '#4F46E5', pos: [1, 0] },
                      { k: 'a', label: 'B', color: '#4F46E5', pos: [1, 1] },
                      { k: 'r2', label: 'C', color: '#4F46E5', pos: [1, 2] }],
              l1: 'L', r1: 'R', start: true, select: false, tint: '#3730A3' },
    dc: { name: 'Dreamcast', dpad: true,
          faces: fourDiamond(XBOX, { a: 'A', b: 'B', x: 'X', y: 'Y' }),
          l2: 'L', r2: 'R', start: true, select: false, sticks: ['left'], tint: '#F97316' },
    dreamcast: { extends: 'dc' },
    ngc: { name: 'GameCube', dpad: true,
           faces: [{ k: 'a', label: 'A', color: '#22C55E', pos: [1, 1] },
                   { k: 'b', label: 'B', color: '#DC2626', pos: [2, 1] },
                   { k: 'y', label: 'Y', color: '#CBD5E1', pos: [0, 0] },
                   { k: 'x', label: 'X', color: '#CBD5E1', pos: [1, 2] }],
           l1: 'L', r1: 'R', r2: 'Z', start: true, select: false,
           sticks: ['left', 'right'], tint: '#6D28D9' },
    wii: { extends: 'ngc', name: 'Wii' },
    '3ds': { name: 'Nintendo 3DS', dpad: true,
             faces: fourDiamond(XBOX, { a: 'A', b: 'B', x: 'X', y: 'Y' }),
             l1: 'L', r1: 'R', start: true, select: true, sticks: ['left'], tint: '#0EA5E9' },
    nds: { name: 'Nintendo DS', dpad: true,
           faces: fourDiamond(XBOX, { a: 'A', b: 'B', x: 'X', y: 'Y' }),
           l1: 'L', r1: 'R', start: true, select: true, tint: '#DB2777' },
    'nintendo-dsi': { extends: 'nds' },

    // --- arcade ---
    arcade: { name: 'Arcade', dpad: true,
              faces: [{ k: 'a', label: '1', color: '#F59E0B', pos: [0, 0] },
                      { k: 'b', label: '2', color: '#EF4444', pos: [0, 1] },
                      { k: 'x', label: '3', color: '#3B82F6', pos: [0, 2] },
                      { k: 'y', label: '4', color: '#10B981', pos: [1, 0] },
                      { k: 'l1', label: '5', color: '#8B5CF6', pos: [1, 1] },
                      { k: 'r1', label: '6', color: '#EC4899', pos: [1, 2] }],
              start: true, select: true, coin: true, sixbtn: true, tint: '#111827' },
    mame: { extends: 'arcade' },
    naomi: { extends: 'arcade' },
    atomiswave: { extends: 'arcade' },
    neogeoaes: { name: 'Neo Geo', dpad: true,
                 faces: [{ k: 'b', label: 'A', color: '#DC2626', pos: [1, 0] },
                         { k: 'a', label: 'B', color: '#F59E0B', pos: [1, 1] },
                         { k: 'y', label: 'C', color: '#16A34A', pos: [1, 2] },
                         { k: 'x', label: 'D', color: '#2563EB', pos: [0, 2] }],
                 start: true, select: true, tint: '#0C4A6E' },
    neogeomvs: { extends: 'neogeoaes' },
  };

  function resolve(slug) {
    let l = LAYOUTS[(slug || '').toLowerCase()];
    // follow `extends` chains, merging so a child can override name/fields
    const chain = [];
    while (l && l.extends) { chain.unshift(l); l = LAYOUTS[l.extends]; }
    if (!l) return LAYOUTS.snes;              // sensible default
    let merged = Object.assign({}, l);
    for (const c of chain) merged = Object.assign({}, merged, c);
    delete merged.extends;
    return merged;
  }

  global.RemoteLayouts = { resolve, LAYOUTS };
})(this);
