import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#12151a",
        paper: "#fbfaf8",
      },
    },
  },
  plugins: [],
};
export default config;
