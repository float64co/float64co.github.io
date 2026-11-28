// A readout of where each frame's time goes. Off until var_perf=1.
const EVERY = 500; // ms between updates

export function createPerf(element, renderer, now = () => performance.now()) {
  let on = false;
  let spent = new Map(); // section -> ms since the last update
  let frames = 0;
  let worst = 0;
  let since = null;
  let last = null;

  // How long the graphics card takes, where the browser is willing to say.
  const gl = renderer.getContext?.();
  const timer = gl?.getExtension?.('EXT_disjoint_timer_query_webgl2');
  let query = null;
  let waiting = false;
  let card = { total: 0, count: 0 };

  function collect() {
    if (!waiting || !gl.getQueryParameter(query, gl.QUERY_RESULT_AVAILABLE)) return;
    if (!gl.getParameter(timer.GPU_DISJOINT_EXT)) {
      card.total += gl.getQueryParameter(query, gl.QUERY_RESULT) / 1e6;
      card.count++;
    }
    gl.deleteQuery(query);
    waiting = false;
    query = null;
  }

  const reset = (at) => {
    spent = new Map();
    card = { total: 0, count: 0 };
    frames = 0;
    worst = 0;
    since = at;
  };

  function report(at) {
    const frame = (at - since) / frames;
    const average = (ms) => (ms / frames).toFixed(1).padStart(5);
    const busy = [...spent.values()].reduce((a, b) => a + b, 0) / frames;
    const { calls, triangles } = renderer.info?.render ?? {};
    const canvas = renderer.domElement;
    element.textContent = [
      `frame ${frame.toFixed(1)} ms (worst ${worst.toFixed(0)})`,
      ...[...spent].map(([name, ms]) => `${name.padEnd(16)}${average(ms)}`),
      `${'idle/waiting'.padEnd(16)}${Math.max(0, frame - busy).toFixed(1).padStart(5)}`,
      `graphics card   ${card.count ? (card.total / card.count).toFixed(1).padStart(5) : '  n/a'}`,
      `draw calls ${calls ?? '?'}`,
      `triangles ${triangles === undefined ? '?' : `${(triangles / 1000).toFixed(0)}k`}`,
      `pixels ${canvas?.width ?? '?'}x${canvas?.height ?? '?'}`,
    ].join('\n');
  }

  return {
    isOn: () => on,
    set(value) {
      on = value;
      element.hidden = !on;
      element.textContent = on ? 'measuring…' : '';
      since = last = null;
    },
    // Run something, counting its time under `name`.
    time(name, work) {
      if (!on) return work();
      const start = now();
      const result = work();
      spent.set(name, (spent.get(name) ?? 0) + now() - start);
      return result;
    },
    // Like time, for the drawing itself, which the graphics card finishes later.
    render(work) {
      if (!on) return work();
      if (timer) collect();
      const start = timer && !waiting;
      if (start) {
        query = gl.createQuery();
        gl.beginQuery(timer.TIME_ELAPSED_EXT, query);
      }
      this.time('submit drawing', work);
      if (start) {
        gl.endQuery(timer.TIME_ELAPSED_EXT);
        waiting = true;
      }
    },
    // Call once at the start of every frame.
    frame() {
      if (!on) return;
      const at = now();
      if (since === null) reset(at);
      else {
        frames++;
        worst = Math.max(worst, at - last);
        if (at - since >= EVERY) {
          report(at);
          reset(at);
        }
      }
      last = at;
    },
  };
}
