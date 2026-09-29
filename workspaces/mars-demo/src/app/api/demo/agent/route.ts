import { NextRequest } from 'next/server';
import { readAgentConfig } from '@demo/server/config';
import { AgentConfigurationError, AgentUsageError, askVertex } from '@demo/server/vertex';
import { json, readBoundedJson, requireDemoSession, requireSameOrigin } from '@demo/server/http';
import { usageSnapshot } from '@demo/server/store';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

function quotaError(message: string): { code: string; used?: number; limit?: number } | null {
  if (message.startsWith('DEMO_AGENT_SESSION_LIMIT')) return { code: 'SESSION_DAILY_LIMIT' };
  if (message.startsWith('DEMO_AGENT_GLOBAL_LIMIT')) return { code: 'GLOBAL_DAILY_LIMIT' };
  if (message.startsWith('DEMO_AGENT_CONCURRENT_LIMIT')) return { code: 'GENERATION_BUSY' };
  if (message.startsWith('DEMO_AGENT_RATE_LIMIT')) return { code: 'REQUEST_RATE_LIMIT' };
  return null;
}

export async function POST(request: NextRequest) {
  const originError = requireSameOrigin(request);
  if (originError) return originError;
  const identity = requireDemoSession(request);
  if (!identity) return json({ error: { code: 'DEMO_SESSION_REQUIRED', message: '세션이 만료되었습니다. 다시 시작해 주세요.' } }, 401);
  const body = await readBoundedJson(request, 16_384);
  if (!body.ok) return json({ error: { code: body.reason, message: '질문 입력을 확인해 주세요.' } }, body.reason === 'TOO_LARGE' ? 413 : 400);
  if (!body.value || typeof body.value !== 'object' || Array.isArray(body.value)) {
    return json({ error: { code: 'INVALID_REQUEST', message: '질문 입력을 확인해 주세요.' } }, 400);
  }
  const rawQuestion = (body.value as { question?: unknown }).question;
  if (typeof rawQuestion !== 'string' || !rawQuestion.trim()) {
    return json({ error: { code: 'QUESTION_REQUIRED', message: '질문을 입력해 주세요.' } }, 400);
  }

  let config;
  try {
    config = readAgentConfig();
  } catch {
    return json({ error: { code: 'AGENT_CONFIGURATION_INVALID', message: 'Agent 설정을 사용할 수 없습니다.' } }, 503);
  }
  const question = rawQuestion.trim();
  if (Array.from(question).length > config.maxInputChars) {
    return json({ error: { code: 'QUESTION_TOO_LONG', message: `질문은 ${config.maxInputChars}자 이내로 입력해 주세요.` } }, 413);
  }

  try {
    const answer = await askVertex(identity.session, question, config, new Date());
    return json({ answer: answer.answer, sources: answer.sources, usage: answer.usage, daily: answer.daily });
  } catch (error) {
    const message = error instanceof Error ? error.message : '';
    if (error instanceof AgentConfigurationError) {
      return json({ error: { code: 'AGENT_NOT_CONFIGURED', message: 'Agent 연결을 사용할 수 없습니다.' } }, 503);
    }
    if (message === 'DEMO_AGENT_INPUT_BUDGET') {
      return json({ error: { code: 'QUESTION_CONTEXT_TOO_LARGE', message: '질문과 참고 자료가 요청 한도를 넘었습니다. 더 짧게 질문해 주세요.' } }, 413);
    }
    const quota = quotaError(message);
    if (quota) {
      const daily = usageSnapshot(identity.hash, config, new Date());
      const explanation = quota.code === 'SESSION_DAILY_LIMIT'
        ? '이 세션의 오늘 질문 한도를 사용했습니다.'
        : quota.code === 'GLOBAL_DAILY_LIMIT'
          ? '전체 질문 사용 한도에 도달했습니다.'
          : quota.code === 'GENERATION_BUSY'
            ? '다른 질문을 처리 중입니다. 잠시 뒤 다시 시도해 주세요.'
            : '요청이 너무 잦습니다. 잠시 뒤 다시 시도해 주세요.';
      return json({ error: { ...quota, message: explanation }, daily }, 429);
    }
    if (error instanceof AgentUsageError) {
      const daily = usageSnapshot(identity.hash, config, new Date());
      return json({ error: { code: 'PROVIDER_USAGE_UNAVAILABLE', message: '응답 사용량을 확인하지 못해 답변을 전달하지 않았습니다.' }, daily }, 502);
    }
    const daily = usageSnapshot(identity.hash, config, new Date());
    const outcomeUnknown = message === 'DEMO_VERTEX_OUTCOME_UNKNOWN';
    const providerFailure = message === 'DEMO_VERTEX_REQUEST_FAILED';
    return json({
      error: {
        code: outcomeUnknown ? 'PROVIDER_OUTCOME_UNKNOWN' : providerFailure ? 'PROVIDER_UNAVAILABLE' : 'AGENT_UNAVAILABLE',
        message: outcomeUnknown
          ? '제공자 응답을 확인하지 못했습니다. 요청 횟수는 보수적으로 사용 처리했습니다.'
          : 'Agent가 지금 응답하지 않습니다. 다른 화면은 계속 사용할 수 있습니다.',
      },
      daily,
    }, 502);
  }
}
