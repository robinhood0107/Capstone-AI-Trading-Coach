import type { Config } from 'tailwindcss';
import fullConfig from '../experience-dashboard/tailwind.config';

const config: Config = {
  ...fullConfig,
  content: [
    './src/**/*.{ts,tsx}',
    '../experience-dashboard/src/**/*.{ts,tsx}',
  ],
};

export default config;
