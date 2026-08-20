import { Camera } from 'lucide-react';
import { useTranslations } from 'use-intl';

import type { ViewerSettings } from '../../../core/viewer-settings';
import { TransportMenu } from './transport-menu';

import { Separator } from '@/components/ui/separator';
import { Switch } from '@/components/ui/switch';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';

interface ReplayCameraMenuProps {
  open: boolean;
  camera: ViewerSettings['replayCamera'];
  orthoCameraEnabled: boolean;
  onOpenChange: (open: boolean) => void;
  onHoverChange: (hovered: boolean) => void;
  onCameraChange: (camera: ViewerSettings['replayCamera']) => void;
  onOrthoCameraEnabledChange: (enabled: boolean) => void;
}

export function ReplayCameraMenu({
  open,
  camera,
  orthoCameraEnabled,
  onOpenChange,
  onHoverChange,
  onCameraChange,
  onOrthoCameraEnabledChange,
}: ReplayCameraMenuProps) {
  const t = useTranslations('viewer.transport');
  const tc = useTranslations('common');
  const cameraT = useTranslations('settings.camera');

  function selectCamera(value: string) {
    switch (value) {
      case 'static':
      case 'follow':
      case 'first-person':
        onCameraChange(value);
    }
  }

  return (
    <TransportMenu
      open={open}
      label={t('replayCamera')}
      icon={Camera}
      triggerClassName="max-sm:hidden"
      className="w-44 p-2"
      onOpenChange={onOpenChange}
      onHoverChange={onHoverChange}
    >
      <div className="flex items-center justify-between gap-3 px-1 py-1.5">
        <label className="text-sm" htmlFor="transport-ortho-camera">
          {cameraT('orthoCamera')}
        </label>
        <Switch
          id="transport-ortho-camera"
          aria-label={cameraT('showOrthoCamera')}
          checked={orthoCameraEnabled}
          onCheckedChange={onOrthoCameraEnabledChange}
        />
      </div>
      <Separator className="my-1" />
      <ToggleGroup
        className="flex w-full flex-col"
        type="single"
        orientation="vertical"
        value={camera}
        aria-label={t('replayCamera')}
        onValueChange={selectCamera}
      >
        <ToggleGroupItem className="w-full" value="static">
          {tc('static')}
        </ToggleGroupItem>
        <ToggleGroupItem className="w-full" value="follow">
          {tc('follow')}
        </ToggleGroupItem>
        <ToggleGroupItem className="w-full" value="first-person">
          {tc('firstPerson')}
        </ToggleGroupItem>
      </ToggleGroup>
    </TransportMenu>
  );
}
