// frontend/src/hooks/useSettings.ts

import { useState, useEffect } from 'react';
import { BatterySettings, ElectricitySettings, SolarCorrectionSettings } from '../types';
import api from '../lib/api';

export function useSettings() {
  const [batterySettings, setBatterySettings] = useState<BatterySettings | null>(null);
  const [electricitySettings, setElectricitySettings] = useState<ElectricitySettings | null>(null);
  const [solarCorrectionSettings, setSolarCorrectionSettings] = useState<SolarCorrectionSettings | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function loadSettings() {
      try {
        const [batteryRes, electricityRes, solarCorrectionRes] = await Promise.all([
          api.get('/api/settings/battery'),
          api.get('/api/settings/electricity'),
          api.get('/api/settings/solar-correction')
        ]);

        setBatterySettings(batteryRes.data);
        setElectricitySettings(electricityRes.data);
        setSolarCorrectionSettings(solarCorrectionRes.data);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load settings');
        console.error('Settings fetch error:', err);
      } finally {
        setIsLoading(false);
      }
    }

    loadSettings();
  }, []);

  return {
    batterySettings,
    electricitySettings,
    solarCorrectionSettings,
    setSolarCorrectionSettings,
    isLoading,
    error
  };
}
