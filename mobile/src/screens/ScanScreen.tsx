import { CameraView, useCameraPermissions } from 'expo-camera';
import { useRef, useState } from 'react';
import { FlatList, StyleSheet, Text, Vibration, View } from 'react-native';
import { api, confText, Pkg } from '../api';
import { Theme } from '../theme';
import { Btn, Card } from '../ui';

type Result = { key: string; ok: boolean; title: string; sub: string };

export default function ScanScreen({ t, onChange }: { t: Theme; onChange: () => void }) {
  const [permission, requestPermission] = useCameraPermissions();
  const [results, setResults] = useState<Result[]>([]);
  const last = useRef({ data: '', at: 0 });
  const busy = useRef(false);

  const push = (r: Result) => setResults((old) => [r, ...old].slice(0, 30));

  const onScan = async ({ data }: { data: string }) => {
    const now = Date.now();
    // the camera reports the same code many times per second
    if (busy.current || (data === last.current.data && now - last.current.at < 3500)) return;
    last.current = { data, at: now };
    busy.current = true;
    try {
      const o = await api<{ package: Pkg; new: boolean }>('/api/packages', { body: { qr: data } });
      const p = o.package;
      Vibration.vibrate(o.new ? 60 : [0, 40, 60, 40]);
      push({
        key: `${now}`,
        ok: p.confidence !== 'none',
        title: o.new ? `✓ ${p.id} · ${p.name}` : `${p.id} je već skeniran`,
        sub: `${p.geo.label || p.address} · ${confText[p.confidence]}${p.geo.note ? ' · ' + p.geo.note : ''}`,
      });
      onChange();
    } catch (e) {
      push({ key: `${now}`, ok: false, title: 'Greška', sub: (e as Error).message });
    } finally {
      busy.current = false;
    }
  };

  if (!permission) return <View style={{ flex: 1, backgroundColor: t.bg }} />;
  if (!permission.granted) {
    return (
      <View style={[st.center, { backgroundColor: t.bg }]}>
        <Text style={{ color: t.ink, marginBottom: 12, textAlign: 'center' }}>
          Za skeniranje QR etiketa potrebna je kamera.
        </Text>
        <Btn t={t} primary title="Dozvoli kameru" onPress={requestPermission} />
      </View>
    );
  }

  return (
    <View style={{ flex: 1, backgroundColor: t.bg }}>
      <View style={st.camWrap}>
        <CameraView
          style={StyleSheet.absoluteFill}
          facing="back"
          barcodeScannerSettings={{ barcodeTypes: ['qr'] }}
          onBarcodeScanned={onScan}
        />
        <View style={st.frame} pointerEvents="none" />
        <Text style={st.camHint}>Usmeri kameru na QR etiketu paketa</Text>
      </View>
      <FlatList
        style={{ flex: 1 }}
        contentContainerStyle={{ padding: 12 }}
        data={results}
        keyExtractor={(r) => r.key}
        ListEmptyComponent={
          <Card t={t}>
            <Text style={{ color: t.muted }}>
              Otvori QR etikete na računaru (http://&lt;računar&gt;:8000/test-data/…-etikete.html, mod
              „Jedan po jedan“) i skeniraj ih jednu za drugom. Svaki paket se odmah geokodira na
              zgradu.
            </Text>
          </Card>
        }
        renderItem={({ item }) => (
          <View style={[st.row, { borderColor: t.line }]}>
            <Text style={{ color: item.ok ? t.ink : t.bad, fontWeight: '700' }}>{item.title}</Text>
            <Text style={{ color: t.muted, fontSize: 13 }}>{item.sub}</Text>
          </View>
        )}
      />
    </View>
  );
}

const st = StyleSheet.create({
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24 },
  camWrap: { height: '52%', backgroundColor: '#000', overflow: 'hidden' },
  frame: {
    position: 'absolute',
    top: '18%',
    left: '18%',
    right: '18%',
    bottom: '18%',
    borderWidth: 3,
    borderColor: 'rgba(255,255,255,0.85)',
    borderRadius: 18,
  },
  camHint: {
    position: 'absolute',
    bottom: 10,
    alignSelf: 'center',
    color: '#fff',
    backgroundColor: 'rgba(0,0,0,0.45)',
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 8,
    overflow: 'hidden',
  },
  row: { paddingVertical: 8, borderBottomWidth: StyleSheet.hairlineWidth },
});
