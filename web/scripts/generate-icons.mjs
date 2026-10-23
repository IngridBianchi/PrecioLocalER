#!/usr/bin/env node
/**
 * Generate PWA icons from SVG using sharp
 * Run: node scripts/generate-icons.mjs
 * Requires: npm install sharp --save-dev
 */

import { writeFileSync, existsSync, mkdirSync } from 'fs';
import { resolve } from 'path';
import sharp from 'sharp';

const ICON_SIZES = [
  72, 96, 128, 144, 152, 192, 384, 512
];

const SVG_PATH = resolve('src/assets/icons/icon.svg');
const OUTPUT_DIR = resolve('public/icons');

async function generateIcons() {
  if (!existsSync(SVG_PATH)) {
    console.error(`SVG not found at ${SVG_PATH}`);
    process.exit(1);
  }

  if (!existsSync(OUTPUT_DIR)) {
    mkdirSync(OUTPUT_DIR, { recursive: true });
  }

  console.log('Generating PWA icons...');

  for (const size of ICON_SIZES) {
    const outputPath = resolve(OUTPUT_DIR, `icon-${size}x${size}.png`);
    
    await sharp(SVG_PATH)
      .resize(size, size, {
        fit: 'contain',
        background: { r: 11, g: 107, b: 58, alpha: 1 } // #0b6b3a
      })
      .png({ quality: 90, compressionLevel: 9 })
      .toFile(outputPath);
    
    console.log(`  ✓ ${size}x${size} → ${outputPath}`);
  }

  // Also generate maskable icon (for Android)
  const maskableSize = 512;
  await sharp(SVG_PATH)
    .resize(maskableSize, maskableSize, {
      fit: 'contain',
      background: { r: 11, g: 107, b: 58, alpha: 1 }
    })
    .png()
    .toFile(resolve(OUTPUT_DIR, 'icon-maskable-512x512.png'));
  console.log(`  ✓ maskable 512x512`);

  // Generate Apple touch icon
  await sharp(SVG_PATH)
    .resize(180, 180, {
      fit: 'contain',
      background: { r: 11, g: 107, b: 58, alpha: 1 }
    })
    .png()
    .toFile(resolve(OUTPUT_DIR, 'apple-touch-icon.png'));
  console.log(`  ✓ apple-touch-icon 180x180`);

  // Generate favicon.ico (multiple sizes)
  await sharp(SVG_PATH)
    .resize(32, 32, { fit: 'contain', background: { r: 11, g: 107, b: 58, alpha: 1 } })
    .toFile(resolve(OUTPUT_DIR, 'favicon-32x32.png'));
  
  await sharp(SVG_PATH)
    .resize(16, 16, { fit: 'contain', background: { r: 11, g: 107, b: 58, alpha: 1 } })
    .toFile(resolve(OUTPUT_DIR, 'favicon-16x16.png'));
  
  console.log('\n✅ All icons generated!');
  console.log(`📁 Output: ${OUTPUT_DIR}`);
}

generateIcons().catch(err => {
  console.error('Error:', err);
  process.exit(1);
});