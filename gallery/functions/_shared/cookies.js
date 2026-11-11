const invalid = () => { throw new Error('invalid_cookies'); };
const controls = /[\u0000-\u001f\u007f-\u009f]/;

export function toNetscape(input) {
  try {
    const serialized = typeof input === 'string' ? input : JSON.stringify(input);
    if (typeof serialized !== 'string' || new TextEncoder().encode(serialized).length > 64 * 1024) invalid();
    let cookies = input;
    if (typeof input === 'string') {
      const source = input.trimStart();
      if (source.startsWith('[') || source.startsWith('{')) cookies = JSON.parse(source);
      else cookies = input.split(/\r?\n/).filter(line => line.trim() && (!line.startsWith('#') || line.startsWith('#HttpOnly_'))).map(line => {
        const fields = line.replace(/^#HttpOnly_/, '').split('\t');
        if (fields.length !== 7) invalid();
        const [domain, , path, secure, expirationDate, name, value] = fields;
        if (!['TRUE', 'FALSE'].includes(secure) || !/^\d+$/.test(expirationDate)) invalid();
        return { domain, path, secure: secure === 'TRUE', expirationDate: Number(expirationDate), name, value };
      });
    }
    if (!Array.isArray(cookies)) invalid();
    const lines = ['# Netscape HTTP Cookie File'];
    const names = new Set();
    for (const c of cookies) {
      if (!c || typeof c.domain !== 'string') invalid();
      if (c.domain !== 'facebook.com' && !c.domain.endsWith('.facebook.com')) continue;
      const path = c.path ?? '/';
      const expiry = Number(c.expirationDate ?? 0);
      if (typeof c.name !== 'string' || !c.name || typeof c.value !== 'string' ||
          typeof path !== 'string' || !path.startsWith('/') ||
          [c.domain, path, c.name, c.value].some(value => controls.test(value)) ||
          !Number.isFinite(expiry) || expiry < 0 || !Number.isSafeInteger(Math.trunc(expiry))) invalid();
      names.add(c.name);
      lines.push([c.domain, c.domain.startsWith('.') ? 'TRUE' : 'FALSE', path, c.secure ? 'TRUE' : 'FALSE', Math.trunc(expiry), c.name, c.value].join('\t'));
    }
    if (!names.has('c_user') || !names.has('xs')) throw new Error('missing_session_cookies');
    return { text: lines.join('\n') + '\n', count: lines.length - 1 };
  } catch (error) {
    throw new Error(error?.message === 'missing_session_cookies' ? 'missing_session_cookies' : 'invalid_cookies');
  }
}
