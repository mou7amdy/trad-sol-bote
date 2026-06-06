/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        surface: '#0A0A0F',
        card: '#12121A',
        'card-border': '#1E1E2A',
        accent: '#9945FF',
        'accent-hover': '#7C3AED',
        green: '#00FFA3',
        'green-dim': '#00CC82',
        red: '#FF4444',
        'red-dim': '#CC3333',
        yellow: '#FFB800',
        'yellow-dim': '#CC9300',
        'text-primary': '#F5F5F5',
        'text-secondary': '#888899',
        'text-muted': '#555566',
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'slide-up': 'slideUp 0.3s ease-out',
      },
      keyframes: {
        slideUp: {
          '0%': { transform: 'translateY(10px)', opacity: '0' },
          '100%': { transform: 'translateY(0)', opacity: '1' },
        },
      },
    },
  },
  plugins: [],
}
