/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./web/templates/**/*.html",
    "./static/js/**/*.js",
  ],
  theme: {
    extend: {
      colors: {
        bg: "#070708",
        surface: "#0c0c0f",
        border: "rgba(255,255,255,0.07)",
        gold: "#3dd4e0",
        "gold-light": "#b85fa3",
        muted: "#8e8e96",
        text: "#eaeaec",
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "-apple-system", "sans-serif"],
        display: ["Syne", "Inter", "system-ui", "sans-serif"],
      },
      borderRadius: {
        neo: "28px",
      },
    },
  },
  plugins: [],
};
