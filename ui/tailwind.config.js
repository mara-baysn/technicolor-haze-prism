/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        void: '#080C18',
        surface: '#0F1424',
        border: '#1A2038',
        signal: '#F5C518',
        deny: '#E53935',
        allow: '#43A047',
        text: '#E8E6E3',
        muted: '#6B7085',
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'monospace'],
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
