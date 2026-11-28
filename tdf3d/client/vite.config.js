import { readdirSync, readFileSync, writeFileSync } from 'node:fs';

// Lists the music in public/music, so the game knows what there is to play.
function tracklist() {
  return {
    name: 'tracklist',
    generateBundle() {
      let files = [];
      try {
        files = readdirSync(new URL('./public/music', import.meta.url), { recursive: true });
      } catch {
        // No music folder: an empty radio.
      }
      const tracks = files.map((f) => f.replaceAll('\\', '/'))
        .filter((f) => /\.(mp3|ogg|opus|m4a|aac|wav|flac)$/i.test(f)).sort();
      this.emitFile({ type: 'asset', fileName: 'music/tracks.json', source: JSON.stringify(tracks) });
    },
  };
}

// Writes ../index.html, so that the whole project folder can be dropped into a
// static site (such as GitHub Pages) and opened as <site>/<folder>/. It is the
// built page with its files looked for in client/dist.
function rootPage() {
  return {
    name: 'root-page',
    closeBundle() {
      const built = readFileSync(new URL('./dist/index.html', import.meta.url), 'utf8');
      const page = built.replace(/<meta charset="utf-8">/, '$&\n  <base href="client/dist/">');
      if (page === built) throw new Error('could not add <base> to the root index.html');
      writeFileSync(new URL('../index.html', import.meta.url),
        page.replace('<!doctype html>', '<!doctype html>\n<!-- Written by `npm run build` in client/. Edit client/index.html instead. -->'));
    },
  };
}

export default {
  // Relative addresses, so that it works from any folder of a site.
  base: './',
  build: { target: 'es2022', chunkSizeWarningLimit: 6000 },
  plugins: [tracklist(), rootPage()],
};
