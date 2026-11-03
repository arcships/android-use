import {defineConfig} from '@playwright/test';
export default defineConfig({testDir:'tests',timeout:15000,workers:1,
  use:{channel:process.env.PLAYWRIGHT_CHANNEL||'chrome',headless:true,viewport:{width:390,height:844}}});
