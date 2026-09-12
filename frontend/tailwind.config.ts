import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        surface: {
          DEFAULT: "#0b0e14",
          raised: "#12161f",
          border: "#1f2430",
        },
        accent: {
          DEFAULT: "#3fd0a0",
          dim: "#2a8f6f",
        },
        warn: "#e0a93a",
        danger: "#e25c5c",
      },
    },
  },
  plugins: [],
};

export default config;
