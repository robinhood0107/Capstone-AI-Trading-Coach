# artifacts

워크스페이스 간 산출물 교환 폴더. **계약(`contracts/`)을 만족하는 결과물만** 저장하고 원본 코드나 대용량 원시 데이터는 저장하지 않는다.

```text
return-engine/{runId}/
decision-platform/{runId}/
```

로컬 실행으로 생긴 실제 산출물 파일은 `.gitignore`에 의해 커밋되지 않는다(폴더 구조만 유지).
