import Constants from 'expo-constants';

export type Window = { from: string | null; to: string | null } | null;
export type Promise_ = { from: string; to: string } | null;

export type StopPkg = {
  id: string;
  name: string;
  phone: string;
  address: string;
  track: string;
  confidence: string;
  status: string;
  promise?: Promise_;
};

export type Stop = {
  id: string;
  seq: number | null;
  status: string;
  label: string;
  lat: number;
  lon: number;
  window: Window;
  eta: string | null;
  arrival: string | null;
  late_min: number;
  wait_min: number;
  travel_min: number | null;
  attempts: number;
  done_at: string | null;
  packages: StopPkg[];
};

export type Pkg = {
  id: string;
  name: string;
  phone: string;
  address: string;
  place: string;
  window: Window;
  lat: number | null;
  lon: number | null;
  confidence: 'ok' | 'approx' | 'none' | 'manual';
  status: string;
  track: string;
  stop: string | null;
  promise?: Promise_;
  geo: {
    label: string;
    note: string;
    alternatives: { label: string; lat: number; lon: number }[];
  };
};

export type State = {
  now: string;
  clock: 'sim' | 'real';
  settings: {
    depot: { lat: number; lon: number; label: string };
    shift_start: string;
    shift_end: string;
    service_min: number;
  };
  traffic: { factor_now: number; live_ratio: number; source: string | null; profile: boolean };
  courier: { state: string; target: string | null; lat: number | null; lon: number | null };
  packages: Pkg[];
  route: Stop[];
  done: Stop[];
  unplanned: Stop[];
  unlocated: string[];
  plan: null | {
    reason: string;
    solve_ms: number;
    summary?: {
      stops: number;
      packages: number;
      drive_min: number;
      service_min: number;
      end: string | null;
      late_stops: number;
    };
  };
  geom_version: string;
  notifications: { t: string; pkg: string; kind: string; text: string }[];
  log: { t: string; text: string }[];
};

export type Geometry = {
  version: string;
  current: [number, number][];
  rest: [number, number][][];
};

/** Backend URL: EXPO_PUBLIC_API_URL, else the computer that serves the
 * Metro bundle (same IP, port 8000). Editable at runtime in Settings. */
function guessBase(): string {
  const env = process.env.EXPO_PUBLIC_API_URL;
  if (env) return env.replace(/\/$/, '');
  const host = Constants.expoConfig?.hostUri?.split(':')[0];
  return host ? `http://${host}:8000` : 'http://192.168.1.10:8000';
}

let base = guessBase();
export const getBase = () => base;
export const setBase = (b: string) => {
  base = b.trim().replace(/\/$/, '');
};

export async function api<T = any>(
  path: string,
  opts: { method?: string; body?: unknown } = {},
): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 20000);
  try {
    const r = await fetch(base + path, {
      method: opts.method ?? (opts.body !== undefined ? 'POST' : 'GET'),
      headers: { 'Content-Type': 'application/json' },
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      signal: controller.signal,
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error((data as { detail?: string }).detail ?? `HTTP ${r.status}`);
    return data as T;
  } finally {
    clearTimeout(timer);
  }
}

export const hm = (iso?: string | null) => (iso ? iso.slice(11, 16) : '');

export const gmNav = (lat: number, lon: number) =>
  `https://www.google.com/maps/dir/?api=1&destination=${lat},${lon}&travelmode=driving`;

export const confText: Record<string, string> = {
  ok: 'ok',
  approx: 'proveri',
  none: 'nema lokacije',
  manual: 'ručno',
};
