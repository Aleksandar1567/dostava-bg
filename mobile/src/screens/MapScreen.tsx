import { useEffect, useRef, useState } from 'react';
import { Alert, StyleSheet, Text, View } from 'react-native';
import MapView, { LongPressEvent, Marker, Polyline } from 'react-native-maps';
import { api, Geometry, hm, State } from '../api';
import { Theme } from '../theme';
import { Btn } from '../ui';

const BELGRADE = { latitude: 44.815, longitude: 20.41, latitudeDelta: 0.22, longitudeDelta: 0.22 };
const toLL = (p: [number, number]) => ({ latitude: p[0], longitude: p[1] });

function Dot({ text, bg, fg, border, size = 26 }: { text: string; bg: string; fg: string; border: string; size?: number }) {
  return (
    <View
      style={{
        width: size,
        height: size,
        borderRadius: size / 2,
        backgroundColor: bg,
        borderColor: border,
        borderWidth: 2,
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      <Text style={{ color: fg, fontSize: 11, fontWeight: '700' }}>{text}</Text>
    </View>
  );
}

export default function MapScreen({
  t,
  S,
  geom,
  refresh,
}: {
  t: Theme;
  S: State | null;
  geom: Geometry | null;
  refresh: () => Promise<void>;
}) {
  const map = useRef<MapView>(null);
  const fitted = useRef(false);
  const [pinFor, setPinFor] = useState<string | null>(null);

  useEffect(() => {
    if (!S || fitted.current || !map.current) return;
    const pts = [
      ...S.route.map((s) => ({ latitude: s.lat, longitude: s.lon })),
      { latitude: S.settings.depot.lat, longitude: S.settings.depot.lon },
    ];
    if (pts.length > 1) {
      map.current.fitToCoordinates(pts, {
        edgePadding: { top: 60, right: 40, bottom: 60, left: 40 },
        animated: false,
      });
      fitted.current = true;
    }
  }, [S]);

  const onLongPress = async (e: LongPressEvent) => {
    if (!pinFor) return;
    const { latitude, longitude } = e.nativeEvent.coordinate;
    try {
      await api(`/api/packages/${encodeURIComponent(pinFor)}/location`, {
        method: 'PUT',
        body: { lat: latitude, lon: longitude },
      });
      setPinFor(null);
      await refresh();
    } catch (err) {
      Alert.alert('Greška', (err as Error).message);
    }
  };

  const unlocated = S ? S.packages.filter((p) => p.lat == null && p.status === 'pending') : [];

  return (
    <View style={{ flex: 1 }}>
      <MapView
        ref={map}
        style={StyleSheet.absoluteFill}
        initialRegion={BELGRADE}
        showsUserLocation
        onLongPress={onLongPress}
      >
        {geom?.rest.map((line, i) => (
          <Polyline key={`r${i}`} coordinates={line.map(toLL)} strokeColor="rgba(11,107,203,0.55)" strokeWidth={3} />
        ))}
        {geom && geom.current.length > 1 ? (
          <Polyline coordinates={geom.current.map(toLL)} strokeColor="#6f42c1" strokeWidth={6} />
        ) : null}

        {S ? (
          <Marker
            coordinate={{ latitude: S.settings.depot.lat, longitude: S.settings.depot.lon }}
            title={S.settings.depot.label}
            tracksViewChanges={false}
          >
            <View style={st.depot}>
              <Text style={{ color: '#fff', fontWeight: '800' }}>M</Text>
            </View>
          </Marker>
        ) : null}

        {S?.done.map((s) => (
          <Marker
            key={`d${s.id}`}
            coordinate={{ latitude: s.lat, longitude: s.lon }}
            title={s.label}
            description={`${s.status === 'done' ? 'Isporučeno' : 'Neuspešno'} ${hm(s.done_at)}`}
            tracksViewChanges={false}
          >
            <Dot text="" size={14} bg={s.status === 'done' ? '#1a7f37' : '#777'} fg="#fff" border="#fff" />
          </Marker>
        ))}

        {S?.route.map((s, i) => (
          <Marker
            key={`s${s.id}-${s.seq}-${i === 0}-${s.late_min > 0}`}
            coordinate={{ latitude: s.lat, longitude: s.lon }}
            title={`${s.seq}. ${s.label}`}
            description={`ETA ${hm(s.eta)}${s.window ? ` · prozor ${s.window.from ?? '…'}–${s.window.to ?? '…'}` : ''}${s.late_min ? ` · kasni ${s.late_min} min` : ''} · ${s.packages.map((p) => p.name).join(', ')}`}
            tracksViewChanges={false}
            zIndex={i === 0 ? 1000 : 1}
          >
            <Dot
              text={`${s.seq}`}
              size={i === 0 ? 32 : 26}
              bg={i === 0 ? '#0b6bcb' : '#fff'}
              fg={i === 0 ? '#fff' : s.late_min ? '#c62828' : '#0b6bcb'}
              border={s.late_min ? '#c62828' : s.window ? '#f0a24a' : '#0b6bcb'}
            />
          </Marker>
        ))}

        {S && S.courier.lat != null && (S.courier.state !== 'depot' || S.clock === 'sim') ? (
          <Marker
            key={`c${S.courier.lat.toFixed(5)},${S.courier.lon?.toFixed(5)}`}
            coordinate={{ latitude: S.courier.lat, longitude: S.courier.lon ?? 0 }}
            title="Kurir"
            tracksViewChanges={false}
            zIndex={2000}
          >
            <Dot text="🚚" size={30} bg="#6f42c1" fg="#fff" border="#fff" />
          </Marker>
        ) : null}
      </MapView>

      {unlocated.length ? (
        <View style={[st.bar, { backgroundColor: t.card, borderColor: t.line }]}>
          {pinFor ? (
            <>
              <Text style={{ color: t.ink, flex: 1 }}>Dugo pritisni mapu na mestu zgrade za {pinFor}</Text>
              <Btn t={t} small title="Otkaži" onPress={() => setPinFor(null)} />
            </>
          ) : (
            <>
              <Text style={{ color: t.bad, flex: 1 }}>
                Bez lokacije: {unlocated.map((p) => p.id).join(', ')}
              </Text>
              <Btn
                t={t}
                small
                primary
                title="Postavi pin"
                onPress={() => {
                  const p = unlocated[0];
                  const hint = p.geo.alternatives[0];
                  if (hint) {
                    map.current?.animateToRegion(
                      { latitude: hint.lat, longitude: hint.lon, latitudeDelta: 0.01, longitudeDelta: 0.01 },
                      400,
                    );
                  }
                  setPinFor(p.id);
                  Alert.alert(p.id, `${p.address}${p.place ? ', ' + p.place : ''}\n${p.geo.note}`);
                }}
              />
            </>
          )}
        </View>
      ) : null}
    </View>
  );
}

const st = StyleSheet.create({
  depot: {
    width: 26,
    height: 26,
    borderRadius: 6,
    backgroundColor: '#222',
    borderColor: '#fff',
    borderWidth: 2,
    alignItems: 'center',
    justifyContent: 'center',
  },
  bar: {
    position: 'absolute',
    top: 10,
    left: 10,
    right: 10,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    borderWidth: 1,
    borderRadius: 12,
    padding: 10,
  },
});
