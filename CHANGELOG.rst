
2026-02-20
==========

V0.5.1
 Removed
 -------
 - get_user_credits methhod from credit_service.
 - Cache key related to get_user_credits method.
 - get_user_credits methhod from credit_service.

 Added
 -----
 - get_user_credits_info method for getting user credits. ( replaces get_user_credits)
 Changed
 -------
 - Converted runtime memory db to singleton class
 - Optimized middleware to read header directly instead of body parssing for used credits.

 Fixed
 -----
 - pypi publish worker
 - fixes in examples.
