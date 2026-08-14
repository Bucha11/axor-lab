// Build-time environment the Vite client exposes. Declared locally (rather than
// via vite/client) because the tsconfig pins an explicit `types` list.
interface ImportMetaEnv {
  /** Base URL of the axor-identity login service (default `/identity`). */
  readonly VITE_IDENTITY_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
