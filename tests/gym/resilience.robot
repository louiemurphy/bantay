*** Settings ***
Documentation    Resilience suite run against the mutation gym.
...
...              These tests assert on the tier that resolved each element rather
...              than merely that it resolved. On the unmutated control page every
...              locator must resolve DIRECT; if it does not, the registry has
...              drifted from the fixture and the figures in reports/gym/ are not
...              valid.
Resource         ../../resources/keywords/browser.resource
Library          bantay.BantayLibrary    locators=resources/locators/gym_checkout.yaml
...              ai=off    propose_patches=${FALSE}
Library          Process
Suite Setup      Start Gym
Suite Teardown   Stop Gym
Test Teardown    Close Browser Safely

*** Variables ***
${GYM_URL}       ${EMPTY}

*** Keywords ***
Start Gym
    ${port} =    Set Variable    8801
    Set Suite Variable    ${GYM_URL}    http://127.0.0.1:${port}
    # `--port` and its value stay separate arguments: written as `--port=${port}`
    # the `=` makes Robot treat it as a named argument to Start Process, which
    # rejects it before argparse ever sees it.
    #
    # sys.executable rather than bare `python`, so the subprocess uses the same
    # interpreter as the suite rather than whichever one is first on PATH.
    ${python} =    Evaluate    sys.executable    modules=sys
    Start Process    ${python}    -m    bantay.gym.server    --port    ${port}
    ...              alias=gym    stdout=${TEMPDIR}/gym.log
    Wait Until Keyword Succeeds    10x    0.3s    Gym Should Be Serving

Gym Should Be Serving
    # urllib rather than curl: `%{...}` is Robot's environment-variable syntax, so
    # curl's `%{http_code}` would be expanded by Robot and never reach the shell.
    # This also removes the curl dependency, and urlopen raises until the server is
    # up, which is what Wait Until Keyword Succeeds needs.
    ${status} =    Evaluate
    ...    urllib.request.urlopen("${GYM_URL}/health", timeout=2).status
    ...    modules=urllib.request
    Should Be Equal As Integers    ${status}    200

Stop Gym
    Run Keyword And Ignore Error    Terminate Process    gym

Open Gym Page At Seed
    [Arguments]    ${seed}
    Open Browser To    ${GYM_URL}/?seed=${seed}

Open Gym Page With Mutations
    [Documentation]    Apply named operators explicitly rather than relying on a
    ...                seed to produce them. plan_for_seed draws 1-3 operators from
    ...                the whole pool, so a seed chosen because it once produced a
    ...                class rename stops producing one as soon as ALL_MUTATIONS is
    ...                reordered, leaving the test asserting something its own DOM
    ...                no longer contains.
    [Arguments]    ${mutations}
    Open Browser To    ${GYM_URL}/?seed=0&mutations=${mutations}

*** Test Cases ***
Control Page Resolves Every Locator Directly
    [Documentation]    The calibration test. Seed 0 applies no mutations, so any
    ...                tier other than DIRECT means the registry has drifted from
    ...                the fixture and no other result here can be trusted.
    [Tags]    control    critical
    Open Gym Page At Seed    0
    ${keys} =    Registered Locator Keys
    FOR    ${key}    IN    @{keys}
        Resolution Tier Should Be    ${key}    DIRECT
    END
    No Locator Drift Should Have Occurred

Class Rename Does Not Break Semantic Locators
    [Documentation]    A CSS framework migration must not cost us a single test.
    ...                Our primary strategies are all semantic, so this should
    ...                resolve DIRECT with no healing at all.
    [Tags]    cosmetic
    Open Gym Page With Mutations    rename_classes
    Resolution Tier Should Be    email    DIRECT
    Resolution Tier Should Be    place_order    DIRECT

Stripped Test Hooks Fall Back Rather Than Fail
    [Documentation]    With data-test hooks deleted, the declared fallback chain
    ...                should carry the resolution. Recovery is expected here -
    ...                what matters is that it is reported, not silent.
    [Tags]    semantic
    Open Gym Page With Mutations    drop_test_hooks
    # Asserting the tier rather than merely that something resolved: a non-None
    # check also passes on a page where nothing has fallen back at all.
    Resolution Tier Should Be    email    FALLBACK
    Resolution Tier Should Be    place_order    FALLBACK
    ${stats} =    Get Resolution Stats
    Log    Resolution tiers: ${stats}

Order Can Be Placed On A Mutated Page
    [Documentation]    End-to-end behaviour under corruption. The point is that a
    ...                *user journey* survives, not just element lookup.
    [Tags]    journey
    Open Gym Page At Seed    ${303}
    Type Into Registered Element    email    tester@example.com
    Type Into Registered Element    postcode    4000
    Click Registered Element    place_order
    Registered Element Should Be Visible    confirmation
    ${text} =    Get Registered Element Text    confirmation
    Should Contain    ${text}    Order placed

Promo Code Changes The Order Total
    [Documentation]    A real assertion on product behaviour: applying the promo
    ...                must reduce the total by exactly 10%.
    [Tags]    journey
    Open Gym Page At Seed    ${0}
    ${before} =    Get Registered Element Text    total
    Click Registered Element    apply_promo
    ${after} =    Get Registered Element Text    total
    ${expected} =    Evaluate    round(float("${before}") * 0.9, 2)
    Should Be Equal As Numbers    ${after}    ${expected}

Unknown Locator Key Fails With A Useful Message
    [Documentation]    Error-handling edge case. A typo in a key is a different
    ...                class of problem from a page change, and must not be
    ...                mistaken for one or sent down the healing path.
    [Tags]    edge    negative
    Open Gym Page At Seed    ${0}
    ${error} =    Run Keyword And Expect Error    *No locator registered*
    ...           Resolve Element    checkout.emial
    Should Contain    ${error}    Did you mean
