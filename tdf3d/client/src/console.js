// Drop-down developer console, toggled with the ` key. Tab completes names.
export function createConsole(commands, variables, onToggle) {
  const root = document.getElementById('console');
  const log = root.querySelector('pre');
  const list = root.querySelector('ul');
  const input = root.querySelector('input');
  const history = [];
  let cursor = 0;
  const menu = createCompletion([...Object.keys(variables), 'help', ...Object.keys(commands)]);

  function print(text) {
    log.textContent += text + '\n';
    log.scrollTop = log.scrollHeight;
  }

  // The graphical list of completions.
  function draw() {
    list.hidden = !menu.matches.length;
    list.replaceChildren(...menu.matches.map((name, n) => {
      const item = document.createElement('li');
      item.className = n === menu.index ? 'selected' : '';
      const variable = variables[name];
      for (const text of [name, variable ? String(variable.get()) : '',
        variable?.help ?? commands[name]?.usage ?? 'list commands']) {
        const cell = document.createElement('span');
        cell.textContent = text;
        item.append(cell);
      }
      // mousedown, so the input keeps focus.
      item.addEventListener('mousedown', (e) => {
        e.preventDefault();
        input.value = name;
        menu.close();
        draw();
      });
      return item;
    }));
    list.querySelector('.selected')?.scrollIntoView({ block: 'nearest' });
  }

  function move(step) {
    input.value = menu.move(step);
    draw();
  }

  function toggle() {
    root.hidden = !root.hidden;
    menu.close();
    draw();
    if (root.hidden) input.blur();
    else input.focus();
    onToggle(!root.hidden);
  }

  addEventListener('keydown', (e) => {
    if (e.code !== 'Backquote') return;
    e.preventDefault(); // don't type the ` into the prompt
    if (!e.repeat) toggle();
  });

  // Typing narrows an open list.
  input.addEventListener('input', () => {
    if (!menu.matches.length) return;
    menu.open(input.value);
    draw();
  });

  input.addEventListener('keydown', (e) => {
    const open = menu.matches.length > 0;
    if (e.code === 'Tab') {
      e.preventDefault();
      if (open) move(e.shiftKey ? -1 : 1);
      else if (menu.open(input.value)) move(1);
    } else if (e.code === 'Escape') {
      if (!open) return toggle();
      menu.close();
      draw();
    } else if (e.code === 'ArrowUp' || e.code === 'ArrowDown') {
      e.preventDefault();
      const step = e.code === 'ArrowUp' ? -1 : 1;
      if (open) return move(step);
      cursor = Math.max(0, Math.min(history.length, cursor + step));
      input.value = history[cursor] ?? '';
    } else if (e.code === 'Enter') {
      menu.close();
      draw();
      const line = input.value.trim();
      input.value = '';
      if (!line) return;
      history.push(line);
      cursor = history.length;
      print('> ' + line);
      print(run(commands, variables, line));
    }
  });

  print('Type "help" for commands, or part of a name and Tab for settings.');
  return { isOpen: () => !root.hidden };
}

// Which names are on offer, and which one is selected.
export function createCompletion(names) {
  const menu = {
    matches: [],
    index: -1,
    // Any part of a name matches; names that start with the text come first.
    open(text) {
      const part = text.trim().toLowerCase();
      const found = names.filter((name) => name.includes(part));
      menu.matches = [...found.filter((name) => name.startsWith(part)),
        ...found.filter((name) => !name.startsWith(part))];
      menu.index = -1;
      return menu.matches.length > 0;
    },
    move(step) {
      const n = menu.matches.length;
      menu.index = menu.index < 0 ? (step > 0 ? 0 : n - 1) : (menu.index + step + n) % n;
      return menu.matches[menu.index];
    },
    close() {
      menu.matches = [];
      menu.index = -1;
    },
  };
  return menu;
}

export function run(commands, variables, line) {
  const assignment = /^(var_\w+)\s*(?:=\s*(.*))?$/.exec(line.trim());
  if (assignment) {
    const [, name, value] = assignment;
    if (!Object.hasOwn(variables, name)) return `unknown variable: ${name}`;
    const variable = variables[name];
    if (value !== undefined && !variable.set(value.trim()))
      return `cannot set ${name}: ${variable.help}`;
    return `${name} = ${variable.get()}`;
  }
  const [name, ...args] = line.trim().split(/\s+/);
  if (name === 'help')
    return [...Object.values(commands).map((c) => c.usage),
      'var_name           show a setting', 'var_name=value     change it',
      'var_ then Tab      list settings'].join('\n');
  if (!Object.hasOwn(commands, name)) return `unknown command: ${name}`;
  return commands[name].run(args);
}
