*** Settings ***
Documentation    Login behaviour on a public site.
...
...              Weighted towards negative and edge cases. The happy path is cheap
...              to cover and rarely regresses; most defects show up in how an
...              application responds to invalid input.
Resource         ../../resources/keywords/browser.resource
Library          bantay.BantayLibrary    locators=resources/locators/saucedemo.yaml
Library          Collections
Suite Teardown   Close Browser Safely
Test Setup       Open Browser To    ${BASE_URL}
Test Teardown    Close Browser Safely
Force Tags       public

*** Variables ***
${BASE_URL}      https://www.saucedemo.com
${VALID_USER}    standard_user
${PASSWORD}      secret_sauce

*** Keywords ***
Attempt Login
    [Arguments]    ${username}    ${password}
    Run Keyword If    '${username}' != '${EMPTY}'
    ...    Type Into Registered Element    username    ${username}
    Run Keyword If    '${password}' != '${EMPTY}'
    ...    Type Into Registered Element    password    ${password}
    Click Registered Element    login_button

Login Should Have Failed With
    [Arguments]    ${expected}
    Registered Element Should Be Visible    login_error
    ${message} =    Get Registered Element Text    login_error
    Should Contain    ${message}    ${expected}
    ${location} =    Get Location
    Should Not Contain    ${location}    inventory
    ...    msg=A rejected login must not navigate to the inventory page

*** Test Cases ***
Valid Credentials Reach The Inventory Page
    [Tags]    smoke
    Attempt Login    ${VALID_USER}    ${PASSWORD}
    Wait Until Location Contains    inventory.html    timeout=10s
    Registered Element Should Be Visible    sort_dropdown

Wrong Password Is Rejected
    [Tags]    negative
    Attempt Login    ${VALID_USER}    definitely-not-the-password
    Login Should Have Failed With    do not match

Locked Out User Is Told Why
    [Documentation]    A rejected login and a locked account are different states
    ...                and must not produce the same message.
    [Tags]    negative
    Attempt Login    locked_out_user    ${PASSWORD}
    Login Should Have Failed With    locked out

Empty Credentials Are Rejected
    [Tags]    negative    edge
    Attempt Login    ${EMPTY}    ${EMPTY}
    Login Should Have Failed With    Username is required

Password Without Username Is Rejected
    [Tags]    negative    edge
    Attempt Login    ${EMPTY}    ${PASSWORD}
    Login Should Have Failed With    Username is required

Whitespace Only Username Is Not Treated As Valid
    [Documentation]    Edge case worth an explicit test: applications that trim
    ...                inconsistently will accept "   " somewhere in the stack.
    [Tags]    negative    edge
    Attempt Login    ${SPACE * 5}    ${PASSWORD}
    Registered Element Should Be Visible    login_error

Every Locator On This Page Resolves Without Healing
    [Documentation]    Guards the registry against the site being redesigned. If
    ...                this fails, reports/patches/ holds a proposed fix to review.
    [Tags]    registry
    Resolution Tier Should Be    username    DIRECT
    Resolution Tier Should Be    password    DIRECT
    Resolution Tier Should Be    login_button    DIRECT
    No Locator Drift Should Have Occurred
