/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        void: '#0A0A0A',
        surface: '#141414',
        border: '#222222',
        signal: '#F5C518',
        deny: '#E53935',
        allow: '#43A047',
        text: '#E8E6E3',
        muted: '#888888',
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'monospace'],
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
