import * as Location from 'expo-location';
import { StatusBar } from 'expo-status-bar';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Pressable, StyleSheet, Text, View } from 'react-native';
import { SafeAreaProvider, useSafeAreaInsets } from 'react-native-safe-area-context';
import { api, Geometry, hm, State } from './src/api';
import MapScreen from './src/screens/MapScreen';
import RouteScreen from './src/screens/RouteScreen';
import ScanScreen from './src/screens/ScanScreen';
import SettingsScreen from './src/screens/SettingsScreen';
import { useTheme } from './src/theme';

type Tab = 'scan' | 'route' | 'map' | 'settings';
const TABS: { key: Tab; icon: string; label: string }[] = [
  { key: 'scan', icon: '📷', label: 'Skeniraj' },
  { key: 'route', icon: '🧭', label: 'Ruta' },
  { key: 'map', icon: '🗺️', label: 'Mapa' },
  { key: 'settings', icon: '⚙️', label: 'Podešavanja' },
];

function Main() {
  const t = useTheme();
  const insets = useSafeAreaInsets();
  const [tab, setTab] = useState<Tab>('route');
  const [S, setS] = useState<State | null>(null);
  const [geom, setGeom] = useState<Geometry | null>(null);
  const [gpsOn, setGpsOn] = useState(false);
  const [gpsInfo, setGpsInfo] = useState('');
  const geomVersion = useRef<string | null>(null);
  const busy = useRef(false);

  const refresh = useCallback(async () => {
    if (busy.current) return;
    busy.current = true;
    try {
      const st = await api<State>('/api/state');
      setS(st);
      if (st.geom_version !== geomVersion.current) {
        geomVersion.current = st.geom_version;
        setGeom(await api<Geometry>('/api/geometry'));
      }
    } catch {
      setS(null);
      geomVersion.current = null;
    } finally {
      busy.current = false;
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 4000);
    return () => clearInterval(id);
  }, [refresh]);

  // GPS -> backend while the switch is on (foreground only, works in Expo Go)
  useEffect(() => {
    if (!gpsOn) return;
    let sub: Location.LocationSubscription | null = null;
    let stopped = false;
    (async () => {
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== 'granted') {
        setGpsOn(false);
        Alert.alert('GPS', 'Bez dozvole za lokaciju ETA se računa samo po planu.');
        return;
      }
      const s = await Location.watchPositionAsync(
        { accuracy: Location.Accuracy.High, timeInterval: 10000, distanceInterval: 25 },
        (loc) => {
          const { latitude, longitude, accuracy } = loc.coords;
          setGpsInfo(
            `Poslednja pozicija ${new Date(loc.timestamp).toLocaleTimeString()} · ±${Math.round(accuracy ?? 0)} m`,
          );
          api('/api/courier/position', { body: { lat: latitude, lon: longitude } })
            .then(refresh)
            .catch((e) => setGpsInfo(`Server: ${(e as Error).message}`));
        },
      );
      if (stopped) s.remove();
      else sub = s;
    })();
    return () => {
      stopped = true;
      sub?.remove();
    };
  }, [gpsOn, refresh]);

  return (
    <View style={{ flex: 1, backgroundColor: t.bg, paddingTop: insets.top }}>
      <View style={[st.top, { borderColor: t.line, backgroundColor: t.card }]}>
        <Text style={{ color: t.ink, fontSize: 17 }}>
          Dostava <Text style={{ fontWeight: '800' }}>BG</Text>
        </Text>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          {S?.clock === 'sim' ? (
            <Text style={[st.badge, { backgroundColor: t.sim }]}>SIMULACIJA</Text>
          ) : null}
          {gpsOn ? <Text style={[st.badge, { backgroundColor: t.ok }]}>GPS</Text> : null}
          <Text style={{ color: S ? t.ink : t.bad, fontWeight: '700', fontSize: 16 }}>
            {S ? hm(S.now) : 'nema servera'}
          </Text>
        </View>
      </View>

      <View style={{ flex: 1 }}>
        {tab === 'scan' && <ScanScreen t={t} onChange={refresh} />}
        {tab === 'route' && <RouteScreen t={t} S={S} refresh={refresh} />}
        {tab === 'map' && <MapScreen t={t} S={S} geom={geom} refresh={refresh} />}
        {tab === 'settings' && (
          <SettingsScreen
            t={t}
            S={S}
            refresh={refresh}
            gpsOn={gpsOn}
            setGpsOn={setGpsOn}
            gpsInfo={gpsInfo}
          />
        )}
      </View>

      <View
        style={[
          st.tabs,
          { borderColor: t.line, backgroundColor: t.card, paddingBottom: Math.max(insets.bottom, 6) },
        ]}
      >
        {TABS.map((x) => (
          <Pressable key={x.key} style={st.tab} onPress={() => setTab(x.key)}>
            <Text style={{ fontSize: 20, opacity: tab === x.key ? 1 : 0.55 }}>{x.icon}</Text>
            <Text
              style={{
                fontSize: 11,
                fontWeight: '600',
                color: tab === x.key ? t.acc : t.muted,
              }}
            >
              {x.label}
              {x.key === 'route' && S?.route.length ? ` (${S.route.length})` : ''}
            </Text>
          </Pressable>
        ))}
      </View>
      <StatusBar style="auto" />
    </View>
  );
}

export default function App() {
  return (
    <SafeAreaProvider>
      <Main />
    </SafeAreaProvider>
  );
}

const st = StyleSheet.create({
  top: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  badge: {
    color: '#fff',
    fontSize: 11,
    fontWeight: '800',
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: 999,
    overflow: 'hidden',
  },
  tabs: { flexDirection: 'row', borderTopWidth: StyleSheet.hairlineWidth, paddingTop: 6 },
  tab: { flex: 1, alignItems: 'center', gap: 2 },
});
