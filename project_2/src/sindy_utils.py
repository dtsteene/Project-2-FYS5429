import jax.numpy as jnp
from scipy.special import binom
from scipy.integrate import odeint
from itertools import combinations_with_replacement


def library_size(
    n: int, poly_order: int, use_sine: bool = False, include_constant=True
) -> int:
    """
    Calculate the size of the library of functions for the given number of states and polynomial order

    Args:
        n: int, number of states/features
        poly_order: int, the maximum order of the polynomial terms
        use_sine: bool, whether to include the sine terms
        include_constant: bool, whether to include the constant term

    Returns:
        l: int, the size of the library

    -Iterates through each polynomial order and finds the number of combinations with replacement ( n + k - 1 choose k)
    for the case where n = 3. Each state could for instance represent a spacial cooardinate x, y, z. Where for each
    polynomial order k, we want to find x^a * y^b * z^c where a + b + c = k. This is equivalent to finding the number of
    ways to distribute k identical balls into n distinct boxes. This is given by the binomial coefficient (n + k - 1 choose k).

    -If use_sine is True, then it adds n sine terms.

    -If include_constant is False, then it subtracts 1 from the total size of the library
    """
    l = 0
    for k in range(poly_order + 1):
        l += int(binom(n + k - 1, k))
    if use_sine:
        l += n
    if not include_constant:
        l -= 1
    return l


def sindy_library(
    features: jnp.ndarray,
    poly_order: int,
    lib_size: int,
) -> jnp.ndarray:
    """
    Generate the SINDy library for discovering first order ODEs.
    
    Args:
        X: jnp.array of shape (m, n), m is the number of samples, n is the number of states
        poly_order: int, the maximum order of the polynomial terms
    
    Returns:
        library: jnp.array of shape (m, l) where l is the size of the library, i.e the number of functions
        that we attempt to fit the data to
    
    """
     
    m, n = features.shape #num_samples, num_features
    
    num_features = n
    l = lib_size
    library = jnp.ones((m, l))
    index = 1
        
    library[:, index: index + num_features] = features

    for current_order in range(2, poly_order + 1):
        for term_indices in combinations_with_replacement(
            range(num_features), current_order
        ):
            product = jnp.prod(features[:, term_indices], axis=1)
            library[:, index] = product
            index += 1
    
    return library



def add_sine(X_prime, library: jnp.ndarray) -> jnp.ndarray:
    """
    Add sine functions to the library of functions.
    
    Args:
        library_size: int, the size of the library before adding sine functions
        X_prime: The feature matrix. jnp.ndarray of shape (m, n), m is the number of samples, n is the number of states
        library: jnp.ndarray of shape (m, l), the library of functions
        
    Returns:
        library: jnp.ndarray of shape (m, l), the library of functions with sine functions added
            
    """
    lib_size = library.shape[1]
    num_features = X_prime.shape[1]
    index = int(lib_size - num_features)
    for i in range(num_features):
            library[:, index] = jnp.sin(X_prime[:, i])
            index += 1
    return library


def sindy_fit(RHS, LHS, coefficient_threshold):
    """

    Fit the SINDy model coefficients using a least squares fit with thresholding sparsity.

    Args:
        RHS: jnp.ndarray, right-hand side of the SINDy model - library matrix of candidate functions
        LHS: jnp.ndarray, left-hand side of the SINDy model - matrix of time derivatives
        coefficient_threshold: float, the threshold below which coefficients are considered to be zero

    Returns:
        Xi: jnp.ndarray, sparse matrix of coefficients where coefficients below the threshold
        are zeroed out. These coefficients represent the terms in the governing equations
        associated with the library of functions

    - This function performs sparse regression using a thresholding method. Starting with an
      initial least squares solution, it zeroes out coefficients with magnitude below the
      specified threshold and then refines the remaining non-zero coefficients by performing
      least squares again on the non-zero elements. The zeroing and refinement steps are repeated
      multiple times to enhance sparsity in the solution.

    """
    m, n = LHS.shape
    Xi = jnp.linalg.lstsq(RHS, LHS, rcond=None)[0]

    for k in range(10):
        small_inds = jnp.abs(Xi) < coefficient_threshold
        Xi[small_inds] = 0
        for i in range(n):
            big_inds = ~small_inds[:, i]
            if jnp.where(big_inds)[0].size == 0:
                continue
            Xi[big_inds, i] = jnp.linalg.lstsq(RHS[:, big_inds], LHS[:, i], rcond=None)[
                0
            ]
    return Xi


def sindy_simulate(x0, t, Xi, poly_order, include_sine):
    """
    Simulate the discovered dynamical system from initial conditions using the SINDy coefficients.

    Args:
        x0: jnp.ndarray, initial state of the system
        t: jnp.ndarray, time points where the solution is sought (must be 1D array)
        Xi: jnp.ndarray, matrix of SINDy coefficients used for simulation
        poly_order: int, the polynomial order used in the function library
        include_sine: bool, whether to include sine in the function library

    Returns:
        x: jnp.ndarray, array of model states over time points

    - Utilizes the `odeint` function from scipy.integrate to simulate the system of ODEs
      represented by the function library and sparse coefficients found by SINDy.
    - Constructs a function representing the right-hand side of the ODEs by taking the dot
      product of the function library applied to the current state with the Xi coefficients.
    """

    n = x0.size
    if include_sine:
        sindy_library = lambda X_prime, poly_order, lib_size: add_sine(X_prime, sindy_library(X_prime, poly_order, lib_size))
    
    lib_size = library_size(n, poly_order, include_sine)
    
    def f(x, t):
        return jnp.dot(
            sindy_library(jnp.array(x).reshape((1, n)), poly_order,lib_size=lib_size), Xi
    ).reshape((n,))

    x = odeint(f, x0, t)
    return x


def sindy_simulate_order2(x0, dx0, t, Xi, poly_order, include_sine):
    """
    Simulate the second-order dynamical system specified by the SINDy coefficients.

    Args:
        x0: jnp.ndarray, initial state vector of the system
        dx0: jnp.ndarray, initial derivative of the state vector
        t: jnp.ndarray, time points for the simulation
        Xi: jnp.ndarray, SINDy coefficients for the first-order system
        poly_order: int, order of the polynomials in the library
        include_sine: bool, flag to include sine function in the library

    Returns:
        x: jnp.ndarray, the simulated states of the system at the requested time points

    - It prepares an extended set of SINDy coefficients to account for both the state and its derivatives.
    - Uses the `sindy_simulate` function internally to perform the simulation with the expanded initial conditions and coefficients.
    """

    n = 2 * x0.size
    l = Xi.shape[0]

    Xi_order1 = jnp.zeros((l, n))
    for i in range(n // 2):
        Xi_order1[2 * (i + 1), i] = 1.0
        Xi_order1[:, i + n // 2] = Xi[:, i]

    x = sindy_simulate(
        jnp.concatenate((x0, dx0)), t, Xi_order1, poly_order, include_sine
    )
    return x
