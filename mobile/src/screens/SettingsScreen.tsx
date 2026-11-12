import { useEffect, useRef, useState } from 'react';
import { Alert, ScrollView, StyleSheet, Switch, Text, TextInput, View } from 'react-native';
import { api, getBase, hm, setBase, State } from '../api';
import { Theme } from '../theme';
import { Btn, Card } from '../ui';

/** Health + test datasets of the current server; never throws. */
async function probe(): Promise<{ health: string; datasets: string[] }> {
  try {
    const h = await api<{ addresses: number; osrm: string }>('/api/health');
    const datasets = await api<string[]>('/api/datasets');
    return { health: `✓ Povezano · ${h.addresses.toLocaleString()} adresa u registru`, datasets };
  } catch (e) {
    return { health: `✕ ${getBase()}: ${(e as Error).message}`, datasets: [] };
  }
}

export default function SettingsScreen({
  t,
  S,
  refresh,
  gpsOn,
  setGpsOn,
  gpsInfo,
}: {
  t: Theme;
  S: State | null;
  refresh: () => Promise<void>;
  gpsOn: boolean;
  setGpsOn: (v: boolean) => void;
  gpsInfo: string;
}) {
  const [url, setUrl] = useState(getBase());
  const [health, setHealth] = useState('');
  const [datasets, setDatasets] = useState<string[]>([]);
  const [auto, setAuto] = useState(false);
  const autoRef = useRef(false);

  const check = async () => {
    setBase(url);
    const r = await probe();
    setHealth(r.health);
    setDatasets(r.datasets);
    await refresh();
  };

  useEffect(() => {
    let alive = true;
    probe().then((r) => {
      if (!alive) return;
      setHealth(r.health);
      setDatasets(r.datasets);
    });
    return () => {
      alive = false;
      autoRef.current = false;
    };
  }, []);

  const run = async (fn: () => Promise<unknown>, ok?: string) => {
    try {
      await fn();
      await refresh();
      if (ok) Alert.alert(ok);
    } catch (e) {
      Alert.alert('Greška', (e as Error).message);
    }
  };

  const toggleAuto = () => {
    const on = !autoRef.current;
    autoRef.current = on;
    setAuto(on);
    const tick = async () => {
      if (!autoRef.current) return;
      try {
        await api('/api/sim/step', { body: { minutes: 2 } });
        await refresh();
      } catch (e) {
        autoRef.current = false;
        setAuto(false);
        Alert.alert('Simulacija', (e as Error).message);
        return;
      }
      setTimeout(tick, 900);
    };
    if (on) tick();
  };

  return (
    <ScrollView style={{ flex: 1, backgroundColor: t.bg }} contentContainerStyle={{ padding: 12 }}>
      <Card t={t}>
        <Text style={[st.h, { color: t.ink }]}>Server</Text>
        <TextInput
          value={url}
          onChangeText={setUrl}
          autoCapitalize="none"
          autoCorrect={false}
          keyboardType="url"
          style={[st.input, { color: t.ink, borderColor: t.line, backgroundColor: t.bg }]}
        />
        <Btn t={t} primary title="Poveži" onPress={check} />
        <Text style={{ color: health.startsWith('✓') ? t.ok : t.bad, marginTop: 8 }}>{health}</Text>
        <Text style={[st.hint, { color: t.muted }]}>
          Adresa računara na kom radi backend (port 8000). Telefon i računar moraju da budu na istoj
          mreži, ili koristi tunel (README).
        </Text>
      </Card>

      <Card t={t}>
        <Text style={[st.h, { color: t.ink }]}>Simulacija dana</Text>
        <Text style={{ color: t.muted, marginBottom: 8 }}>
          {S ? (S.clock === 'sim' ? `Simulirani sat: ${hm(S.now)}` : 'Sat je stvaran') : ''}
        </Text>
        <View style={st.row}>
          <Btn t={t} primary title="Pokreni" onPress={() => run(() => api('/api/sim/start', { body: {} }))} />
          <Btn t={t} title="+5" onPress={() => run(() => api('/api/sim/step', { body: { minutes: 5 } }))} />
          <Btn t={t} title="+15" onPress={() => run(() => api('/api/sim/step', { body: { minutes: 15 } }))} />
          <Btn t={t} title="+60" onPress={() => run(() => api('/api/sim/step', { body: { minutes: 60 } }))} />
        </View>
        <View style={st.row}>
          <Btn t={t} title={auto ? '⏸ Pauza' : '▶ Auto'} onPress={toggleAuto} />
          <Btn
            t={t}
            title="Stvarni sat"
            onPress={() => {
              autoRef.current = false;
              setAuto(false);
              run(() => api('/api/sim/stop', { body: {} }));
            }}
          />
        </View>
      </Card>

      <Card t={t}>
        <Text style={[st.h, { color: t.ink }]}>GPS (stvarna vožnja)</Text>
        <View style={[st.row, { alignItems: 'center' }]}>
          <Switch value={gpsOn} onValueChange={setGpsOn} />
          <Text style={{ color: t.ink, flex: 1 }}>Šalji moju lokaciju serveru</Text>
        </View>
        <Text style={[st.hint, { color: t.muted }]}>
          {gpsInfo ||
            'Kad je sat stvaran, ETA se računa od tvoje pozicije. Kad se udaljiš 150 m od magacina, dan kreće sam, a na 80 m od adrese stanica se označava kao „stigao“.'}
        </Text>
      </Card>

      <Card t={t}>
        <Text style={[st.h, { color: t.ink }]}>Test podaci</Text>
        {datasets.map((d) => (
          <Btn
            key={d}
            t={t}
            title={`Učitaj ${d}`}
            style={{ marginBottom: 8 }}
            onPress={() => run(() => api(`/api/demo/${d}`, { body: {} }), `Učitan skup ${d}`)}
          />
        ))}
        <Btn
          t={t}
          danger
          title="Novi dan (obriši sve pakete)"
          onPress={() =>
            Alert.alert('Novi dan', 'Obrisati sve pakete i rutu?', [
              { text: 'Ne' },
              { text: 'Obriši', style: 'destructive', onPress: () => run(() => api('/api/reset', { body: {} })) },
            ])
          }
        />
      </Card>
    </ScrollView>
  );
}

const st = StyleSheet.create({
  h: { fontSize: 16, fontWeight: '700', marginBottom: 8 },
  hint: { fontSize: 12, marginTop: 8 },
  input: { borderWidth: 1, borderRadius: 10, padding: 10, marginBottom: 8, fontSize: 15 },
  row: { flexDirection: 'row', gap: 8, flexWrap: 'wrap', marginBottom: 8 },
});
