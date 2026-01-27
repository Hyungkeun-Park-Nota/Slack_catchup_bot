import os
from anthropic import Anthropic
from catchup import CatchupResult, Message


SUMMARY_SYSTEM_PROMPT = """당신은 Slack 채널 메시지를 요약하는 전문가입니다.
주어진 메시지들을 분석하여 아래 형식에 맞게 구조화된 요약을 제공하세요.

규칙:
1. 각 항목은 핵심만 간결하게 작성
2. 모든 항목에 원본 메시지 링크를 [원본↗](링크) 형식으로 포함
3. 중요도가 높은 메시지(리액션/답글 많음)를 우선적으로 포함
4. 해당 카테고리에 내용이 없으면 해당 섹션 생략
5. 메시지가 없거나 요약할 내용이 없으면 "특별한 업데이트가 없습니다" 반환

출력 형식:
🔴 *액션 필요*
• 내용 [원본↗](링크)

📌 *의사결정 사항*
• 내용 [원본↗](링크)

📢 *공지/변경*
• 내용 [원본↗](링크)

💬 *주요 논의*
• 내용 [원본↗](링크)"""


class Summarizer:
    """Claude API를 이용한 메시지 요약기"""
    
    MAX_TOKENS = 100000  # Claude 입력 토큰 제한 (여유 있게)
    
    def __init__(self, api_key: str = None):
        self.client = Anthropic(api_key=api_key or os.getenv('ANTHROPIC_API_KEY'))
    
    def _build_messages_context(self, result: CatchupResult) -> str:
        """요약을 위한 메시지 컨텍스트 구성"""
        lines = []
        
        for msg in result.messages:
            importance_marker = "★" if msg.importance_score >= 5 else ""
            
            lines.append(f"{importance_marker}[{msg.user_name}] (답글:{msg.reply_count}, 리액션:{msg.reaction_count})")
            lines.append(f"내용: {msg.text}")
            lines.append(f"링크: {msg.permalink}")
            
            # 쓰레드 내용 포함
            if msg.thread_messages:
                lines.append("쓰레드:")
                for thread_msg in msg.thread_messages:
                    lines.append(f"  └─ [{thread_msg.user_name}]: {thread_msg.text}")
            
            lines.append("---")
        
        return "\n".join(lines)
    
    def _estimate_tokens(self, text: str) -> int:
        """토큰 수 대략적 추정 (한글 기준 글자당 ~2토큰)"""
        return len(text) * 2
    
    def summarize(self, result: CatchupResult) -> str:
        """채널 메시지 요약 생성"""
        
        if result.error:
            return f"❌ 오류 발생: {result.error}"
        
        if not result.messages:
            return f"📭 #{result.channel_name} 채널에 해당 기간 동안 새 메시지가 없습니다."
        
        context = self._build_messages_context(result)
        
        # 토큰 제한 체크
        if self._estimate_tokens(context) > self.MAX_TOKENS:
            # 중요도 순으로 정렬 후 자르기
            sorted_messages = sorted(
                result.messages, 
                key=lambda m: m.importance_score, 
                reverse=True
            )
            # 상위 메시지만 포함
            truncated_result = CatchupResult(
                channel_name=result.channel_name,
                messages=sorted_messages[:200],
                start_time=result.start_time,
                end_time=result.end_time,
                total_count=result.total_count
            )
            context = self._build_messages_context(truncated_result)
            truncation_notice = f"\n\n⚠️ 메시지가 많아 중요도 높은 상위 200개만 요약했습니다. (전체: {result.total_count}개)"
        else:
            truncation_notice = ""
        
        user_prompt = f"""다음은 #{result.channel_name} 채널의 메시지입니다.
기간: {result.start_time} ~ {result.end_time}
총 {len(result.messages)}개의 메시지

{context}

위 메시지들을 구조화된 형식으로 요약해주세요."""
        
        try:
            response = self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=4096,
                system=SUMMARY_SYSTEM_PROMPT,
                messages=[
                    {"role": "user", "content": user_prompt}
                ]
            )
            
            summary = response.content[0].text
            
            # 헤더 추가
            header = f"📬 *#{result.channel_name}* 요약 ({result.start_time} ~ {result.end_time})\n\n"
            
            return header + summary + truncation_notice
            
        except Exception as e:
            return f"❌ 요약 생성 중 오류 발생: {str(e)}"
    
    def summarize_multiple(self, results: list[CatchupResult]) -> str:
        """여러 채널 요약을 하나로 합치기"""
        summaries = []
        
        for result in results:
            summary = self.summarize(result)
            summaries.append(summary)
            summaries.append("\n" + "─" * 40 + "\n")
        
        return "\n".join(summaries)
