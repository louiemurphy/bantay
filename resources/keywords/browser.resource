*** Settings ***
Documentation    Browser lifecycle. Isolated here so that no suite hard-codes
...              driver options and every suite gets the same settings: a fixed
...              window size and a pinned locale.
Library          SeleniumLibrary    timeout=10s    implicit_wait=0s
Library          OperatingSystem

*** Variables ***
${BROWSER}          headlesschrome
${WINDOW_WIDTH}     1280
${WINDOW_HEIGHT}    900

*** Keywords ***
Open Browser To
    [Documentation]    Open ${url} with a fixed window size and locale.
    ...                implicit_wait is set to 0 in the library import: mixing
    ...                implicit and explicit waits produces timeouts that are hard
    ...                to predict, so only explicit waits are used.
    [Arguments]    ${url}
    Open Browser    ${url}    ${BROWSER}
    Set Window Size    ${WINDOW_WIDTH}    ${WINDOW_HEIGHT}

Close Browser Safely
    [Documentation]    Teardown that cannot itself fail the suite.
    Run Keyword And Ignore Error    Close All Browsers
