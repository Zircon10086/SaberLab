import { z } from 'zod';

import type { EnvironmentData } from './types';

const environmentDataSchema = z.custom<EnvironmentData>(
  (value) => z.looseObject({ version: z.literal(1), id: z.string() }).safeParse(value).success,
);

/**
 * 加载环境 JSON 数据。
 *
 * 原版使用独立 environment worker；在 SaberLab 内嵌场景（iframe + 多 worker 并发
 * beatmap/lzma/environment）下出现过 worker 挂起（消息不返回、请求未发出）导致
 * 查看器卡死的现象。环境 JSON 仅 ~0.5MB，主线程直接 fetch+解析开销可接受，
 * 且彻底消除 worker 生命周期/竞态风险。
 */
export async function loadEnvironmentData(id: string, signal?: AbortSignal): Promise<EnvironmentData> {
  const url = `${import.meta.env.BASE_URL}environments/${id}.json`;
  // 防御：环境数据加载挂起时 5s 超时回退，避免查看器永久卡死
  const timeout = new Promise<never>((_, reject) => {
    setTimeout(() => reject(new Error(`environment ${id} load timed out`)), 5_000);
  });
  const response = await Promise.race([fetch(url, { signal }), timeout]);
  if (!response.ok) {
    throw new Error(`environment ${id} failed to load (${response.status})`);
  }
  const data = environmentDataSchema.parse(await response.json());
  if (data.id !== id) {
    throw new Error(`environment ${id} has invalid metadata`);
  }
  return data;
}
