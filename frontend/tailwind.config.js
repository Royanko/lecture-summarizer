/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./lib/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // BGU Brand Palette
        "bgu-blue":   "#00205B",   // Deep navy — structural: sidebar, headers, nav
        "bgu-orange": "#FF7A00",   // Vibrant orange — CTAs, accents, active states
        // Tints for hover/active states
        "bgu-blue-dark":   "#001540",
        "bgu-blue-light":  "#003080",
        "bgu-orange-dark": "#D96600",
        "bgu-orange-light":"#FF9A33",
      },
      fontFamily: {
        sans: ["var(--font-inter)", "Inter", "system-ui", "sans-serif"],
        display: ["var(--font-syne)", "Syne", "system-ui", "sans-serif"],
        hebrew: ["'Noto Sans Hebrew'", "'David Libre'", "Arial", "sans-serif"],
      },
      boxShadow: {
        card: "0 2px 12px 0 rgba(0,32,91,0.10)",
        "card-hover": "0 8px 32px 0 rgba(0,32,91,0.16)",
        "orange-glow": "0 4px 24px 0 rgba(255,122,0,0.25)",
      },
      borderRadius: {
        xl2: "1rem",
        xl3: "1.5rem",
      },
    },
  },
  plugins: [],
};
