/**
 * Offline font constants — replaces @remotion/google-fonts (which fetches from
 * fonts.gstatic.com at render time and fails/hangs without network).
 *
 * The fonts are installed as SYSTEM fonts (macOS: ~/Library/Fonts; Linux: ~/.fonts
 * or fontconfig), so headless Chrome resolves them by family name with zero
 * loading logic — nothing to fetch, nothing to delayRender, nothing to time out.
 * Source TTFs are kept in public/fonts/ for provisioning new machines:
 *   cp public/fonts/*.ttf ~/Library/Fonts/    (macOS)
 *   cp public/fonts/*.ttf ~/.fonts/ && fc-cache -f    (Linux)
 *
 * Each constant includes safe fallbacks in case the font is not installed.
 */
export const SPACE_GROTESK = "'Space Grotesk', 'Avenir Next', Helvetica, Arial, sans-serif";
export const PLAYFAIR = "'Playfair Display', Georgia, 'Times New Roman', serif";
export const POPPINS = "Poppins, 'Avenir Next', Helvetica, Arial, sans-serif";
export const UNBOUNDED = "Unbounded, 'Avenir Next', Helvetica, sans-serif";
export const MANROPE = "Manrope, 'Avenir Next', Helvetica, sans-serif";
export const INSTRUMENT_SERIF = "'Instrument Serif', Georgia, serif";
