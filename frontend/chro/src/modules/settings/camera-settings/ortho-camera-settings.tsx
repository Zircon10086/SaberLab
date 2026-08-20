import { useTranslations } from 'use-intl';

import type { ViewerSettings } from '../../../core/viewer-settings';
import { SettingSection } from '../components/setting-section';
import type { CameraSettingsUpdate } from './camera-setting-controls';

import { Switch } from '@/components/ui/switch';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';

interface OrthoCameraSettingsProps {
  settings: ViewerSettings;
  update: CameraSettingsUpdate;
}

export function OrthoCameraSettings({ settings, update }: OrthoCameraSettingsProps) {
  const t = useTranslations('settings.camera');

  return (
    <SettingSection
      title={t('orthoCamera')}
      action={
        <Switch
          checked={settings.orthoCameraEnabled}
          aria-label={t('showOrthoCamera')}
          onCheckedChange={(enabled) => {
            update('orthoCameraEnabled', enabled);
          }}
        />
      }
    >
      {settings.orthoCameraEnabled && (
        <div className="flex flex-col gap-2 py-2">
          <p className="text-sm">{t('orthographicView')}</p>
          <ToggleGroup
            className="grid w-full grid-cols-3"
            type="single"
            value={settings.orthoCameraView}
            aria-label={t('orthographicViewLabel')}
            onValueChange={(value) => {
              switch (value) {
                case 'back':
                case 'left':
                case 'right':
                  update('orthoCameraView', value);
              }
            }}
          >
            <ToggleGroupItem className="w-full" value="left">
              {t('leftView')}
            </ToggleGroupItem>
            <ToggleGroupItem className="w-full" value="back">
              {t('backView')}
            </ToggleGroupItem>
            <ToggleGroupItem className="w-full" value="right">
              {t('rightView')}
            </ToggleGroupItem>
          </ToggleGroup>
        </div>
      )}
    </SettingSection>
  );
}
