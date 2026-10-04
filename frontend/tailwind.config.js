/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#102a33",
        mist: "#eef5f4",
        sea: "#1f6f78",
        sand: "#d9c3a2",
        alert: "#9a4a3a",
      },
      fontFamily: {
        display: ['"Source Serif 4"', "Georgia", "serif"],
        sans: ['"IBM Plex Sans"', "Segoe UI", "sans-serif"],
      },
      boxShadow: {
        soft: "0 10px 30px rgba(16, 42, 51, 0.08)",
      },
    },
  },
  plugins: [],
};
