/**
 * ============================================================================
 * 🚀 dart-watch 정시 실행 스케줄러 (Google Apps Script)
 * ============================================================================
 * GitHub Actions의 자체 schedule(cron)이 수 시간씩 밀리는 문제를 해결하기 위해,
 * Google 서버에서 정확한 한국 시간(KST)에 GitHub API(workflow_dispatch)를 호출하여
 * 지연 시간 0초로 즉시 실행시키는 스크립트입니다.
 *
 * [사용 방법 (딱 3분 소요)]
 * 1. GitHub 토큰 발급:
 *    - GitHub 우측 상단 프로필 클릭 -> Settings -> 좌측 맨 아래 Developer settings
 *    - Personal access tokens -> Tokens (classic) -> Generate new token (classic)
 *    - Note: "dart-watch-trigger", Expiration: "No expiration"
 *    - 권한 체크: [v] repo, [v] workflow 체크 후 맨 아래 Generate token 클릭
 *    - 생성된 토큰(ghp_... 형태) 복사
 *
 * 2. Google Apps Script 등록:
 *    - https://script.google.com 접속 -> [새 프로젝트] 클릭
 *    - 기존 코드를 지우고 이 파일의 내용을 그대로 붙여넣기
 *    - 아래 GITHUB_TOKEN 변수에 복사한 토큰(ghp_...) 입력 후 저장(Ctrl+S)
 *
 * 3. 자동 트리거 켜기:
 *    - 상단 실행할 함수 선택 드롭다운에서 [setupTrigger] 선택 후 [실행] 버튼 클릭
 *    - 권한 검토 팝업이 뜨면 [권한 검토] -> 본인 구글 계정 선택 -> [고급] -> [프로젝트(안전하지 않음)로 이동] -> [허용]
 *    - "트리거 설정 완료" 로그가 뜨면 끝! (이후 10분마다 시간을 체크하여 정시에 자동 실행됩니다)
 * ============================================================================
 */

const GITHUB_OWNER = "youngjinrepo";
const GITHUB_REPO = "dart-watch";
const GITHUB_BRANCH = "main";
const GITHUB_TOKEN = "여기에_발급받은_GITHUB_TOKEN을_붙여넣으세요"; // 예: "ghp_xxxxxxxxxxxxxxxxxxxx"

/**
 * 10분마다 실행되며 현재 한국 시간(KST)을 확인해 각 워크플로우를 중복 없이 정시에 깨웁니다.
 */
function schedulerTick() {
  const now = new Date();
  // 한국 시간(Asia/Seoul) 기준 요일, 날짜, 시, 분 추출
  const kstDateStr = Utilities.formatDate(now, "Asia/Seoul", "yyyy-MM-dd");
  const kstHour = parseInt(Utilities.formatDate(now, "Asia/Seoul", "HH"), 10);
  const kstMin = parseInt(Utilities.formatDate(now, "Asia/Seoul", "mm"), 10);
  // 요일: 1(월) ~ 5(금), 6(토), 7(일)
  const kstDayOfWeek = parseInt(Utilities.formatDate(now, "Asia/Seoul", "u"), 10);

  const isWeekday = kstDayOfWeek >= 1 && kstDayOfWeek <= 5; // 월~금
  const isTueToSat = kstDayOfWeek >= 2 && kstDayOfWeek <= 6; // 화~토 (미국장 월~금 마감 직후)

  // 현재 30분 단위 슬롯 계산 (00~29분 -> "00", 30~59분 -> "30")
  const halfHourSlot = kstMin < 30 ? "00" : "30";

  // -------------------------------------------------------------------------
  // 1) DART 공시 알림 (dart.yml): 평일(월~금) 08:00 ~ 19:00, 30분 간격
  // -------------------------------------------------------------------------
  if (isWeekday) {
    const inDartWindow =
      (kstHour >= 8 && kstHour <= 18) || (kstHour === 19 && halfHourSlot === "00");
    if (inDartWindow) {
      const slotKey = `dart_${kstDateStr}_${kstHour}:${halfHourSlot}`;
      triggerOncePerSlot("dart.yml", slotKey);
    }
  }

  // -------------------------------------------------------------------------
  // 2) 공모주(IPO) 스코어링 알림 (ipo.yml): 평일(월~금) 08:30 / 16:00 / 20:00
  // -------------------------------------------------------------------------
  if (isWeekday) {
    const isIpo0830 = kstHour === 8 && kstMin >= 30;
    const isIpo1600 = kstHour === 16 && kstMin < 30;
    const isIpo2000 = kstHour === 20 && kstMin < 30;

    if (isIpo0830) {
      triggerOncePerSlot("ipo.yml", `ipo_${kstDateStr}_08:30`);
    } else if (isIpo1600) {
      triggerOncePerSlot("ipo.yml", `ipo_${kstDateStr}_16:00`);
    } else if (isIpo2000) {
      triggerOncePerSlot("ipo.yml", `ipo_${kstDateStr}_20:00`);
    }
  }

  // -------------------------------------------------------------------------
  // 3) 미국 주식 밸류체인 & SEC 레이더 (us_news.yml): 1시간 간격 (월~토 매시 정각)
  //    (새로 올라온 SEC 공시나 미국 기사가 없으면 AI 호출 없이 1초 만에 조용히 종료됨)
  // -------------------------------------------------------------------------
  const isMonToSat = kstDayOfWeek >= 1 && kstDayOfWeek <= 6;
  if (isMonToSat && halfHourSlot === "00") {
    triggerOncePerSlot("us_news.yml", `us_news_${kstDateStr}_${kstHour}:00`);
  }
}

/**
 * 동일한 시간 슬롯(slotKey)에서는 딱 1번만 GitHub workflow_dispatch를 호출합니다.
 */
function triggerOncePerSlot(workflowFile, slotKey) {
  const props = PropertiesService.getScriptProperties();
  const propName = `LAST_SLOT_${workflowFile}`;
  const lastSlot = props.getProperty(propName);

  if (lastSlot === slotKey) {
    return; // 이미 이 시간대에 실행 완료됨
  }

  const ok = dispatchGitHubWorkflow(workflowFile);
  if (ok) {
    props.setProperty(propName, slotKey);
    Logger.log(`✅ [${workflowFile}] 정시 실행 트리거 완료 (${slotKey})`);
  }
}

/**
 * GitHub REST API를 호출해 workflow_dispatch 이벤트를 즉시 발생시킵니다 (지연 0초).
 */
function dispatchGitHubWorkflow(workflowFile) {
  if (!GITHUB_TOKEN || GITHUB_TOKEN.indexOf("여기에_") !== -1) {
    Logger.log("❌ GITHUB_TOKEN이 설정되지 않았습니다.");
    return false;
  }

  const url = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/workflows/${workflowFile}/dispatches`;
  const options = {
    method: "post",
    contentType: "application/json",
    headers: {
      Authorization: `Bearer ${GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
    },
    payload: JSON.stringify({ ref: GITHUB_BRANCH }),
    muteHttpExceptions: true,
  };

  const response = UrlFetchApp.fetch(url, options);
  const code = response.getResponseCode();
  if (code === 204) {
    return true;
  } else {
    Logger.log(`⚠️ [${workflowFile}] 호출 실패 (HTTP ${code}): ${response.getContentText()}`);
    return false;
  }
}

/**
 * [최초 1회 실행용] 기존 트리거를 정리하고 10분 간격 자동 타이머를 등록합니다.
 */
function setupTrigger() {
  const triggers = ScriptApp.getProjectTriggers();
  for (const t of triggers) {
    ScriptApp.deleteTrigger(t);
  }

  ScriptApp.newTrigger("schedulerTick")
    .timeBased()
    .everyMinutes(10)
    .create();

  Logger.log("🎉 트리거 설정 완료! 이제 10분마다 시간을 체크하여 정시에 GitHub Actions를 즉시 깨웁니다.");
}

/**
 * [연동 테스트용] 이 함수를 선택하고 [실행]을 누르면 즉시 공모주(ipo.yml) 워크플로우가 GitHub에서 켜지는지 테스트할 수 있습니다.
 */
function testTriggerNow() {
  const ok = dispatchGitHubWorkflow("ipo.yml");
  if (ok) {
    Logger.log("🎉 테스트 성공! GitHub Actions 탭을 확인해 보세요 (1~3초 내 즉시 실행됨).");
  }
}
