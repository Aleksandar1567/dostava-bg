import { Alert, FlatList, Linking, RefreshControl, StyleSheet, Text, View } from 'react-native';
import { api, gmNav, hm, State, Stop } from '../api';
import { Theme } from '../theme';
import { Btn, Card, Tag } from '../ui';

function call(phone: string) {
  if (!phone || /^TEST/i.test(phone)) {
    Alert.alert('Test broj', 'Poziv je namerno onemogućen za test pakete.');
    return;
  }
  Linking.openURL(`tel:${phone.replace(/\s+/g, '')}`);
}

export default function RouteScreen({
  t,
  S,
  refresh,
}: {
  t: Theme;
  S: State | null;
  refresh: () => Promise<void>;
}) {
  const run = async (path: string, okMsg?: string) => {
    try {
      await api(path, { body: {} });
      await refresh();
      if (okMsg) Alert.alert(okMsg);
    } catch (e) {
      Alert.alert('Greška', (e as Error).message);
    }
  };

  if (!S) {
    return (
      <View style={[st.center, { backgroundColor: t.bg }]}>
        <Text style={{ color: t.muted }}>Nema veze sa serverom. Proveri adresu u Podešavanjima.</Text>
      </View>
    );
  }

  const next: Stop | undefined = S.route[0];
  const sm = S.plan?.summary;
  const c = S.courier;

  const header = (
    <View>
      {sm ? (
        <View style={st.chips}>
          <Tag t={t} text={`${sm.stops} stanica`} />
          <Tag t={t} text={`${sm.packages} paketa`} />
          <Tag t={t} text={`kraj ${hm(sm.end)}`} />
          <Tag t={t} text={`gužva ×${S.traffic.factor_now.toFixed(2)}`} color={t.warn} />
          {sm.late_stops ? <Tag t={t} text={`kasni ${sm.late_stops}`} color={t.bad} /> : null}
        </View>
      ) : null}
      <View style={st.btnRow}>
        <Btn t={t} primary title="Optimizuj" onPress={() => run('/api/plan')} />
        <Btn t={t} title="Kreni" onPress={() => run('/api/day/start')} />
      </View>
      {!S.plan ? (
        <Card t={t}>
          <Text style={{ color: t.ink }}>
            Još nema rute. Skeniraj pakete ili u Podešavanjima učitaj test skup, pa klikni Optimizuj.
          </Text>
        </Card>
      ) : !next ? (
        <Card t={t}>
          <Text style={{ color: t.ink }}>Sve stanice su obiđene. 🎉</Text>
        </Card>
      ) : (
        <Card t={t} style={{ borderColor: t.acc, borderWidth: 2 }}>
          <Text style={[st.k, { color: t.muted }]}>
            {c.state === 'driving' && c.target === next.id
              ? 'VOZI SE KA'
              : c.state === 'serving' && c.target === next.id
                ? 'NA ADRESI'
                : 'SLEDEĆA STANICA'}{' '}
            · {S.done.length + 1} od {S.done.length + S.route.length}
          </Text>
          <Text style={[st.addr, { color: t.ink }]}>{next.label}</Text>
          <Text style={[st.eta, { color: t.ink }]}>{hm(next.eta)}</Text>
          <Text style={{ color: t.muted }}>
            dolazak {hm(next.arrival)}
            {next.travel_min != null ? ` · vožnja ${next.travel_min} min` : ''}
            {next.wait_min ? ` · čeka prozor ${next.wait_min} min` : ''}
          </Text>
          <View style={st.chips}>
            {next.window ? (
              <Tag t={t} text={`prozor ${next.window.from ?? '…'}–${next.window.to ?? '…'}`} color={t.warn} />
            ) : null}
            {next.late_min ? <Tag t={t} text={`kasni ${next.late_min} min`} color={t.bad} /> : null}
            {next.attempts ? <Tag t={t} text={`pokušaj ${next.attempts + 1}`} /> : null}
          </View>
          {next.packages.map((p) => (
            <View key={p.id} style={st.pkgRow}>
              <Text style={{ color: t.ink, flex: 1 }}>
                {p.id} · {p.name}
              </Text>
              <Btn t={t} small title="Pozovi" onPress={() => call(p.phone)} />
            </View>
          ))}
          <View style={[st.btnRow, { marginTop: 10 }]}>
            <Btn t={t} title="Navigiraj" onPress={() => Linking.openURL(gmNav(next.lat, next.lon))} />
            <Btn
              t={t}
              title="Stigao"
              disabled={c.state === 'serving' && c.target === next.id}
              onPress={() => run(`/api/stops/${next.id}/arrived`)}
            />
          </View>
          <View style={st.btnRow}>
            <Btn t={t} primary title="Isporučeno" onPress={() => run(`/api/stops/${next.id}/delivered`)} style={{ flex: 1 }} />
            <Btn t={t} title="Nije se javio" onPress={() => run(`/api/stops/${next.id}/failed`)} style={{ flex: 1 }} />
          </View>
        </Card>
      )}
    </View>
  );

  return (
    <FlatList
      style={{ flex: 1, backgroundColor: t.bg }}
      contentContainerStyle={{ padding: 12 }}
      data={S.route.slice(1)}
      keyExtractor={(s) => s.id}
      ListHeaderComponent={header}
      refreshControl={<RefreshControl refreshing={false} onRefresh={refresh} />}
      renderItem={({ item }) => (
        <View style={[st.stop, { borderColor: t.line }]}>
          <View style={[st.seq, { borderColor: item.late_min ? t.bad : t.acc }]}>
            <Text style={{ color: item.late_min ? t.bad : t.acc, fontWeight: '700', fontSize: 12 }}>
              {item.seq}
            </Text>
          </View>
          <View style={{ flex: 1 }}>
            <Text style={{ color: t.ink }}>
              {item.label}
              {item.packages.length > 1 ? `  · ${item.packages.length} pak.` : ''}
            </Text>
            <Text style={{ color: t.muted, fontSize: 12 }}>
              {item.packages.map((p) => p.name).join(', ')}
              {item.window ? `  ·  prozor ${item.window.from ?? '…'}–${item.window.to ?? '…'}` : ''}
              {item.late_min ? `  ·  kasni ${item.late_min} min` : ''}
            </Text>
          </View>
          <Text style={{ color: t.ink, fontWeight: '700' }}>{hm(item.eta)}</Text>
        </View>
      )}
    />
  );
}

const st = StyleSheet.create({
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', marginVertical: 6 },
  btnRow: { flexDirection: 'row', gap: 8, marginBottom: 10 },
  k: { fontSize: 12, fontWeight: '700', letterSpacing: 0.4 },
  addr: { fontSize: 20, fontWeight: '700', marginTop: 2 },
  eta: { fontSize: 34, fontWeight: '800' },
  pkgRow: { flexDirection: 'row', alignItems: 'center', marginTop: 6, gap: 8 },
  stop: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingVertical: 9,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  seq: {
    width: 28,
    height: 28,
    borderRadius: 14,
    borderWidth: 2,
    alignItems: 'center',
    justifyContent: 'center',
  },
});
