import React, { useState, useEffect, useCallback } from 'react';
import { BarChart2, RefreshCw } from 'lucide-react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';
import api from '../lib/api';

interface DayProfile {
  date: string;
  periods: (number | null)[];
}

interface LoadProfileResponse {
  days: DayProfile[];
}

const COLORS = ['#6366f1', '#f59e0b', '#10b981', '#ef4444', '#3b82f6', '#8b5cf6', '#ec4899'];

const periodToTime = (period: number): string => {
  const h = Math.floor(period / 4).toString().padStart(2, '0');
  const m = ((period % 4) * 15).toString().padStart(2, '0');
  return `${h}:${m}`;
};

const LoadProfilePage: React.FC = () => {
  const [data, setData] = useState<LoadProfileResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdate, setLastUpdate] = useState<Date>(new Date());

  const fetchData = useCallback(async (showSpinner = false) => {
    if (showSpinner) setLoading(true);
    setError(null);
    try {
      const response = await api.get('/api/load_profile');
      setData(response.data);
      setLastUpdate(new Date());
    } catch (err) {
      setError('Kon load profiel niet laden');
      console.error('Failed to fetch load profile:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData(true);
  }, [fetchData]);

  // Build chart data: one entry per period with all days as keys
  const chartData = React.useMemo(() => {
    if (!data) return [];
    return Array.from({ length: 96 }, (_, period) => {
      const entry: Record<string, number | string | null> = { time: periodToTime(period) };
      data.days.forEach((day) => {
        entry[day.date] = day.periods[period];
      });
      return entry;
    });
  }, [data]);

  return (
    <div className="p-4 space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <BarChart2 className="h-5 w-5 text-indigo-600" />
          <h2 className="text-xl font-semibold text-gray-900 dark:text-white">Load Profiel</h2>
          {data && (
            <span className="text-xs text-gray-500 dark:text-gray-400 ml-2">
              {data.days.length} dagen
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
      ) : data ? (
        <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-4">
          <ResponsiveContainer width="100%" height={400}>
            <LineChart data={chartData} margin={{ top: 4, right: 16, left: 0, bottom: 4 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#374151" opacity={0.3} />
              <XAxis
                dataKey="time"
                tick={{ fontSize: 11 }}
                interval={7}
                stroke="#6b7280"
              />
              <YAxis
                tick={{ fontSize: 11 }}
                stroke="#6b7280"
                unit="W"
                width={55}
              />
              <Tooltip
                formatter={(value: number) => [`${value} W`, '']}
                labelFormatter={(label) => `${label}`}
                contentStyle={{ fontSize: 12 }}
              />
              <Legend />
              {data.days.map((day, idx) => (
                <Line
                  key={day.date}
                  type="monotone"
                  dataKey={day.date}
                  stroke={COLORS[idx % COLORS.length]}
                  dot={false}
                  strokeWidth={1.5}
                  connectNulls={false}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      ) : null}
    </div>
  );
};

export default LoadProfilePage;
