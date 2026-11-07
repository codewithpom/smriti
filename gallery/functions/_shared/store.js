// Storage for data JSON and uploads. Works with a Cloudflare KV namespace (free, no card needed)
// or an R2 bucket (same binding name, PHOTOS). Callers use one small R2-shaped API:
//   get(key) -> {body, httpMetadata, size} | null,  put(key, body, {httpMetadata}),
//   list({prefix, cursor}) -> {objects:[{key,size}], truncated, cursor},  delete(key)
// KV limits to know: 25 MiB per value, ~1,000 writes/day on the free plan, eventually consistent (~60 s).

export function storage(env) {
  const ns = env.PHOTOS;
  if (!ns) throw new Error('storage_not_bound');
  if (typeof ns.getWithMetadata !== 'function') return ns; // R2 bucket: already the shape we want
  return {
    async get(key) {
      const { value, metadata } = await ns.getWithMetadata(key, 'arrayBuffer');
      if (value === null) return null;
      return { body: value, httpMetadata: metadata?.httpMetadata, size: value.byteLength };
    },
    async put(key, body, options = {}) {
      const size = body.byteLength ?? body.length;
      await ns.put(key, body, { metadata: { httpMetadata: options.httpMetadata ?? null, size } });
    },
    async list({ prefix, cursor } = {}) {
      const listed = await ns.list({ prefix, cursor });
      return {
        objects: listed.keys.map(k => ({ key: k.name, size: k.metadata?.size ?? 0 })),
        truncated: !listed.list_complete,
        cursor: listed.cursor,
      };
    },
    delete: key => ns.delete(key),
  };
}
