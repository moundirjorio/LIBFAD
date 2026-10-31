/** Config Tailwind pour le build standalone (sans Node).
    Reproduit la config inline qui était dans base.html (Play CDN). */
module.exports = {
  darkMode: "class",
  content: ["./app/templates/**/*.html"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
      },
      colors: {
        brand: {
          50: "#eef2ff", 100: "#e0e7ff", 200: "#c7d2fe", 300: "#a5b4fc",
          400: "#818cf8", 500: "#6366f1", 600: "#4f46e5", 700: "#4338ca",
          800: "#3730a3", 900: "#312e81",
        },
      },
    },
  },
  // Classes parfois générées dynamiquement (Jinja / JS) — on les force.
  safelist: [
    { pattern: /(text|bg|border)-(green|red|amber|blue|indigo|slate|rose)-(400|500|600)/, variants: ["dark"] },
  ],
  plugins: [],
};
