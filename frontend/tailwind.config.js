/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#0f2740",
        mist: "#eef1f4",
        sea: "#1f7a78",
        sand: "#d9c3a2",
        alert: "#9a4a3a",
      },
      fontFamily: {
        display: ['"Source Serif 4"', "Georgia", "serif"],
        sans: ['"IBM Plex Sans"', "Segoe UI", "sans-serif"],
      },
      boxShadow: {
        card: "0 1px 2px rgba(15, 39, 64, 0.06), 0 8px 24px rgba(15, 39, 64, 0.04)",
      },
    },
  },
  plugins: [],
};
