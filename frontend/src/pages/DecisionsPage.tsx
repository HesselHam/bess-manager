import React, { useState, useEffect, useCallback } from 'react';
import { Calendar, RefreshCw } from 'lucide-react';
import api from '../lib/api';

interface PeriodDetail {
  period: number;
  time: string;
  date?: string;
  dataSource: string;
  isCurrent: boolean;
  buyPrice: number;
  sellPrice: number;
  solarForecast: number;
  consumptionForecast: number;
  soeStart: number;
  soeEnd: number;
  costBasis: number;
  strategicIntent: string;
  batteryAction: number;
  batteryMode: string;
  gridCharge: boolean;
  blockExport: boolean;
  chargeRate: number;
  dischargeRate: number;
  gridImported: number;
  gridExported: number;
  hourlyCost: number;
  gridOnlyCost: number;
  hourlySavings: number;
  actualSoeEnd: number | null;
  actualGridImported: number | null;
  actualGridExported: number | null;
  actualSolarProduction: number | null;
  actualConsumption: number | null;
  actualHourlyCost: number | null;
  actualGridOnlyCost: number | null;
  actualHourlySavings: number | null;
  actualChargeRate: number | null;
  actualDischargeRate: number | null;
  dpReward: number | null;
  dpValue: number | null;
  solarCorrectionFactor: number | null;
  loadSegment: string | null;
}

interface PeriodDetailsResponse {
  periods: PeriodDetail[];
  optimizationPeriod: number | null;
  optimizationTimestamp: string | null;
  currentPeriod: number;
  currency: string;
}

const DecisionsPage: React.FC = () => {
  const [data, setData] = useState<PeriodDetailsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdate, setLastUpdate] = useState<Date>(new Date());

  const fetchData = useCallback(async (showSpinner = false) => {
    if (showSpinner) setLoading(true);
    setError(null);
    try {
      const response = await api.get('/api/period_details');
      setData(response.data);
      setLastUpdate(new Date());
    } catch (err) {
      setError('Kon beslissingsdata niet laden');
      console.error('Failed to fetch period details:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData(true);
  }, [fetchData]);

  const fmtDual = (planned: number, actual: number | null, d = 3, showZero = false) => {
    const fv = (v: number) => (!showZero && v === 0) ? '—' : v.toFixed(d);
    if (actual === null) return <span className="text-gray-400 dark:text-gray-500">{fv(planned)}</span>;
    return (
      <>
        <span className="text-gray-400 dark:text-gray-500">{fv(planned)}</span>
        <span className="text-gray-300 dark:text-gray-600 mx-0.5">/</span>
        <span className="font-medium">{fv(actual)}</span>
      </>
    );
  };

  const fmtCostDual = (planned: number, actual: number | null) => {
    const fv = (v: number) => v === 0 ? '—' : v.toFixed(4);
    if (actual === null) return <>{fv(planned)}</>;
    return (
      <>
        <span className="text-gray-400 dark:text-gray-500">{fv(planned)}</span>
        <span className="text-gray-300 dark:text-gray-600 mx-0.5">/</span>
        <span className="font-medium">{fv(actual)}</span>
      </>
    );
  };

  const currency = data?.currency ?? 'EUR';

  return (
    <div className="p-4 space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Calendar className="h-5 w-5 text-purple-600" />
          <h2 className="text-xl font-semibold text-gray-900 dark:text-white">
            Decision Details
          </h2>
          {data && (
            <span className="text-xs text-gray-500 dark:text-gray-400 ml-2">
              {data.periods.length} periodes
            </span>
          )}
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs text-gray-400 dark:text-gray-500">
            {lastUpdate.toLocaleTimeString()}
          </span>
          <button
            onClick={() => fetchData(true)}
            className="p-1.5 text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 rounded"
            title="Verversen"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {error && (
        <div className="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-700 rounded-lg p-3 text-sm text-red-700 dark:text-red-400">
          {error}
        </div>
      )}

      {loading && !data ? (
        <div className="flex items-center justify-center h-48 text-gray-400">
          <RefreshCw className="h-6 w-6 animate-spin mr-2" />
          Laden…
        </div>
      ) : data && data.periods.length > 0 ? (
        <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700">
          <div className="overflow-x-auto rounded-xl">
            <div className="max-h-[calc(100vh-160px)] overflow-y-auto">
              <table className="min-w-full text-xs divide-y divide-gray-200 dark:divide-gray-700">
                <thead className="bg-gray-50 dark:bg-gray-700 sticky top-0 z-10">
                  <tr>
                    <th className="px-2 py-2 text-left font-semibold text-gray-600 dark:text-gray-300 whitespace-nowrap">Tijd</th>
                    <th className="px-2 py-2 text-left font-semibold text-gray-600 dark:text-gray-300 whitespace-nowrap">Bron</th>
                    <th className="px-2 py-2 text-right font-semibold text-blue-600 dark:text-blue-400 whitespace-nowrap">Inkoop</th>
                    <th className="px-2 py-2 text-right font-semibold text-blue-600 dark:text-blue-400 whitespace-nowrap">Verkoop</th>
                    <th className="px-2 py-2 text-right font-semibold text-yellow-600 dark:text-yellow-400 whitespace-nowrap">Solar</th>
                    <th className="px-2 py-2 text-right font-semibold text-yellow-600 dark:text-yellow-400 whitespace-nowrap" title="plan / werkelijk">Verbruik</th>
                    <th className="px-2 py-2 text-right font-semibold text-green-600 dark:text-green-400 whitespace-nowrap">SOE↑</th>
                    <th className="px-2 py-2 text-right font-semibold text-green-600 dark:text-green-400 whitespace-nowrap" title="plan / werkelijk %">SOE↓</th>
                    <th className="px-2 py-2 text-right font-semibold text-green-600 dark:text-green-400 whitespace-nowrap">Kostprijs</th>
                    <th className="px-2 py-2 text-left font-semibold text-purple-600 dark:text-purple-400 whitespace-nowrap">Intent</th>
                    <th className="px-2 py-2 text-right font-semibold text-purple-600 dark:text-purple-400 whitespace-nowrap">Actie</th>
                    <th className="px-2 py-2 text-left font-semibold text-purple-600 dark:text-purple-400 whitespace-nowrap">Mode</th>
                    <th className="px-2 py-2 text-center font-semibold text-purple-600 dark:text-purple-400 whitespace-nowrap">GridChg</th>
                    <th className="px-2 py-2 text-center font-semibold text-red-600 dark:text-red-400 whitespace-nowrap" title="Export geblokkeerd (verkoopprijs negatief)">BlkExp</th>
                    <th className="px-2 py-2 text-right font-semibold text-purple-600 dark:text-purple-400 whitespace-nowrap" title="plan / werkelijk %">Chg%</th>
                    <th className="px-2 py-2 text-right font-semibold text-purple-600 dark:text-purple-400 whitespace-nowrap" title="plan / werkelijk %">Dchg%</th>
                    <th className="px-2 py-2 text-right font-semibold text-gray-600 dark:text-gray-300 whitespace-nowrap" title="plan / werkelijk kWh">Grid↓</th>
                    <th className="px-2 py-2 text-right font-semibold text-gray-600 dark:text-gray-300 whitespace-nowrap" title="plan / werkelijk kWh">Grid↑</th>
                    <th className="px-2 py-2 text-right font-semibold text-red-600 dark:text-red-400 whitespace-nowrap" title="plan / werkelijk">Cost Grid Only</th>
                    <th className="px-2 py-2 text-right font-semibold text-red-600 dark:text-red-400 whitespace-nowrap" title="plan / werkelijk">Real Cost</th>
                    <th className="px-2 py-2 text-right font-semibold text-red-600 dark:text-red-400 whitespace-nowrap" title="plan / werkelijk">Savings</th>
                    <th className="px-2 py-2 text-right font-semibold text-indigo-600 dark:text-indigo-400 whitespace-nowrap" title="DP reward voor gekozen actie">DP Reward</th>
                    <th className="px-2 py-2 text-right font-semibold text-indigo-600 dark:text-indigo-400 whitespace-nowrap" title="DP waarde functie V[t,i]">V[t,i]</th>
                    <th className="px-2 py-2 text-right font-semibold text-amber-600 dark:text-amber-400 whitespace-nowrap" title="Solar forecast correctiefactor">☀ corr.</th>
                    <th className="px-2 py-2 text-left font-semibold text-teal-600 dark:text-teal-400 whitespace-nowrap" title="Load segment (avond/nacht)">Segment</th>
                  </tr>
                  <tr className="text-gray-400 dark:text-gray-500">
                    <td className="px-2 pb-1"></td>
                    <td className="px-2 pb-1"></td>
                    <td className="px-2 pb-1 text-right">{currency}/kWh</td>
                    <td className="px-2 pb-1 text-right">{currency}/kWh</td>
                    <td className="px-2 pb-1 text-right">kWh</td>
                    <td className="px-2 pb-1 text-right">kWh</td>
                    <td className="px-2 pb-1 text-right">%</td>
                    <td className="px-2 pb-1 text-right">%</td>
                    <td className="px-2 pb-1 text-right">{currency}/kWh</td>
                    <td className="px-2 pb-1"></td>
                    <td className="px-2 pb-1 text-right">kWh</td>
                    <td className="px-2 pb-1"></td>
                    <td className="px-2 pb-1 text-center"></td>
                    <td className="px-2 pb-1 text-center"></td>
                    <td className="px-2 pb-1 text-right">%</td>
                    <td className="px-2 pb-1 text-right">%</td>
                    <td className="px-2 pb-1 text-right">plan/act kWh</td>
                    <td className="px-2 pb-1 text-right">plan/act kWh</td>
                    <td className="px-2 pb-1 text-right">plan/act {currency}</td>
                    <td className="px-2 pb-1 text-right">plan/act {currency}</td>
                    <td className="px-2 pb-1 text-right">plan/act {currency}</td>
                    <td className="px-2 pb-1 text-right">{currency}</td>
                    <td className="px-2 pb-1 text-right">{currency}</td>
                    <td className="px-2 pb-1"></td>
                    <td className="px-2 pb-1"></td>
                  </tr>
                </thead>
                <tbody className="bg-white dark:bg-gray-800 divide-y divide-gray-100 dark:divide-gray-700">
                  {(() => {
                    const rows: React.ReactNode[] = [];
                    let lastDate: string | undefined = undefined;

                    data.periods.forEach((p) => {
                      if (p.date && p.date !== lastDate) {
                        lastDate = p.date;
                        rows.push(
                          <tr key={`date-${p.date}`} className="bg-gray-100 dark:bg-gray-700/60">
                            <td colSpan={25} className="px-2 py-1 text-xs font-semibold text-gray-500 dark:text-gray-400">
                              {p.date}
                            </td>
                          </tr>
                        );
                      }

                      const intentColors: Record<string, string> = {
                        GRID_CHARGING: 'text-blue-700 dark:text-blue-400',
                        SOLAR_STORAGE: 'text-yellow-700 dark:text-yellow-400',
                        LOAD_SUPPORT: 'text-green-700 dark:text-green-400',
                        EXPORT_ARBITRAGE: 'text-red-700 dark:text-red-400',
                        IDLE: 'text-gray-500 dark:text-gray-400',
                      };
                      const intentColor = intentColors[p.strategicIntent] ?? 'text-gray-500';
                      const rowBg = p.isCurrent
                        ? 'bg-blue-50 dark:bg-blue-900/30'
                        : p.dataSource === 'actual'
                        ? 'bg-green-50/40 dark:bg-green-900/10'
                        : '';

                      rows.push(
                        <tr key={p.period} className={`${rowBg} hover:bg-gray-50 dark:hover:bg-gray-700/50`}>
                          <td className="px-2 py-1 font-mono font-medium whitespace-nowrap">
                            {p.time}
                            {p.isCurrent && <span className="ml-1 text-blue-600 font-bold">◀</span>}
                          </td>
                          <td className="px-2 py-1 whitespace-nowrap">
                            <span className={`px-1 py-0.5 rounded text-gray-500 dark:text-gray-400 ${p.dataSource === 'actual' ? 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400' : p.dataSource === 'missing' ? 'bg-red-100 dark:bg-red-900/30 text-red-500' : 'bg-gray-100 dark:bg-gray-700'}`}>
                              {p.dataSource === 'actual' ? 'act' : p.dataSource === 'missing' ? 'miss' : 'prv'}
                            </span>
                          </td>
                          <td className="px-2 py-1 text-right font-mono">{p.buyPrice.toFixed(4)}</td>
                          <td className="px-2 py-1 text-right font-mono">{p.sellPrice.toFixed(4)}</td>
                          <td className="px-2 py-1 text-right font-mono text-yellow-700 dark:text-yellow-400">{fmtDual(p.solarForecast, p.actualSolarProduction)}</td>
                          <td className="px-2 py-1 text-right font-mono">{fmtDual(p.consumptionForecast, p.actualConsumption)}</td>
                          <td className="px-2 py-1 text-right font-mono text-green-700 dark:text-green-400">{p.soeStart.toFixed(1)}%</td>
                          <td className="px-2 py-1 text-right font-mono text-green-700 dark:text-green-400">{fmtDual(p.soeEnd, p.actualSoeEnd, 1)}{'%'}</td>
                          <td className="px-2 py-1 text-right font-mono text-gray-600 dark:text-gray-300">{p.costBasis.toFixed(4)}</td>
                          <td className={`px-2 py-1 whitespace-nowrap font-medium ${intentColor}`}>
                            {p.strategicIntent.replace(/_/g, ' ')}
                          </td>
                          <td className={`px-2 py-1 text-right font-mono font-semibold ${p.batteryAction > 0 ? 'text-blue-700 dark:text-blue-400' : p.batteryAction < 0 ? 'text-orange-700 dark:text-orange-400' : 'text-gray-400'}`}>
                            {p.batteryAction === 0 ? '—' : (p.batteryAction > 0 ? '+' : '') + p.batteryAction.toFixed(3)}
                          </td>
                          <td className="px-2 py-1 whitespace-nowrap text-gray-600 dark:text-gray-300">
                            {p.batteryMode === 'battery_first' ? 'Bat1st' : p.batteryMode === 'grid_first' ? 'Grid1st' : 'Load1st'}
                          </td>
                          <td className="px-2 py-1 text-center">
                            {p.gridCharge ? <span className="text-blue-600">✓</span> : <span className="text-gray-300 dark:text-gray-600">—</span>}
                          </td>
                          <td className="px-2 py-1 text-center">
                            {p.blockExport ? <span className="text-red-600">✓</span> : <span className="text-gray-300 dark:text-gray-600">—</span>}
                          </td>
                          <td className="px-2 py-1 text-right font-mono text-gray-600 dark:text-gray-300">{fmtDual(p.chargeRate, p.actualChargeRate, 0, true)}</td>
                          <td className="px-2 py-1 text-right font-mono text-gray-600 dark:text-gray-300">{fmtDual(p.dischargeRate, p.actualDischargeRate, 0, true)}</td>
                          <td className="px-2 py-1 text-right font-mono text-orange-700 dark:text-orange-400">{fmtDual(p.gridImported, p.actualGridImported)}</td>
                          <td className="px-2 py-1 text-right font-mono text-teal-700 dark:text-teal-400">{fmtDual(p.gridExported, p.actualGridExported)}</td>
                          <td className="px-2 py-1 text-right font-mono text-gray-400">{fmtCostDual(p.gridOnlyCost, p.actualGridOnlyCost)}</td>
                          <td className="px-2 py-1 text-right font-mono">{fmtCostDual(p.hourlyCost, p.actualHourlyCost)}</td>
                          <td className={`px-2 py-1 text-right font-mono font-semibold ${p.hourlySavings > 0 ? 'text-green-700 dark:text-green-400' : p.hourlySavings < 0 ? 'text-red-600 dark:text-red-400' : 'text-gray-400'}`}>
                            {fmtCostDual(p.hourlySavings, p.actualHourlySavings)}
                          </td>
                          <td className={`px-2 py-1 text-right font-mono ${p.dpReward != null && p.dpReward > 0 ? 'text-indigo-700 dark:text-indigo-400' : p.dpReward != null && p.dpReward < 0 ? 'text-red-600 dark:text-red-400' : 'text-gray-400'}`}>
                            {p.dpReward != null ? p.dpReward.toFixed(4) : '—'}
                          </td>
                          <td className="px-2 py-1 text-right font-mono text-indigo-600 dark:text-indigo-400">
                            {p.dpValue != null ? p.dpValue.toFixed(4) : '—'}
                          </td>
                          <td className={`px-2 py-1 text-right font-mono ${p.solarCorrectionFactor != null && p.solarCorrectionFactor !== 1 ? 'text-amber-600 dark:text-amber-400' : 'text-gray-400'}`}>
                            {p.solarCorrectionFactor != null ? p.solarCorrectionFactor.toFixed(3) : '1.000'}
                          </td>
                          <td className="px-2 py-1 text-left font-mono text-teal-600 dark:text-teal-400">
                            {p.loadSegment ?? ''}
                          </td>
                        </tr>
                      );
                    });

                    return rows;
                  })()}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      ) : (
        <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-8 text-center text-gray-500 dark:text-gray-400">
          Geen optimaliseringsdata beschikbaar
        </div>
      )}
    </div>
  );
};

export default DecisionsPage;
